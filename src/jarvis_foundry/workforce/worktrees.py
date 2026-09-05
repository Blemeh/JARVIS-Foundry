"""Exact-SHA worktree preparation and durable mutable-scope ownership."""
from __future__ import annotations

import hashlib
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ..clock import isoformat, parse, utc_now
from ..control.capabilities import set_capability_state
from ..control.registration import inspect_git_resource, registration_event
from ..database import FoundryDatabase
from ..supervisor.leases import LeaseManager, LeaseUnavailable
from .contracts import TERMINAL_SESSION_STATUSES, ResourceClaim, WorkPackage
from .state import (
    SUPERVISOR_CONTROLLER,
    _append,
    _event,
    _gate,
    _rebuild_workforce_state_on_connection,
    rebuild_workforce_state,
    transition_work_package,
)

_CLAIM_EVENT_TYPES = (
    "workforce.resource_claimed",
    "workforce.resource_bound",
    "workforce.resource_renewed",
    "workforce.resource_released",
    "workforce.resource_expired",
)
_DEFAULT_MUTEX_WAIT_SECONDS = 120.0
_DEFAULT_MUTEX_RETRY_SECONDS = 0.05
_DEFAULT_MUTEX_LEASE_SECONDS = 300


class WorkforceResourceError(RuntimeError):
    """A worktree or mutable resource cannot be assigned safely."""


ExecutionWorktreePhase = Literal["builder_admission", "job_delta"]
_EXECUTION_WORKTREE_PHASES = frozenset({
    "builder_admission",
    "job_delta",
})


def _git(root: Path, *args: str, timeout: int = 120) -> str:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorkforceResourceError(
            detail or f"git {' '.join(args)} failed in {root}"
        )
    return result.stdout.strip()


def _branch_exists(repository: Path, branch: str) -> bool:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ],
        capture_output=True,
        timeout=30,
        check=False,
        **headless_run_kwargs(),
    )
    return result.returncode == 0


def _git_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (
        path.resolve(strict=False)
        if path.is_absolute()
        else (root / path).resolve(strict=False)
    )


def _path_identity(value: str | Path) -> str:
    return str(
        Path(value).expanduser().resolve(strict=False)
    ).replace("\\", "/").casefold()


def validate_execution_worktree(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    resource_id: str,
    phase: ExecutionWorktreePhase = "builder_admission",
) -> Path:
    """Bind a WorkPackage to its registered checkout and lifecycle phase."""

    if phase not in _EXECUTION_WORKTREE_PHASES:
        raise ValueError(
            f"unsupported execution worktree validation phase: {phase}"
        )
    requested_resource_id = str(resource_id)
    resource = database.row("resources", "resource_id", requested_resource_id)
    if resource is None:
        raise WorkforceResourceError(
            f"workforce resource is not registered: {resource_id}"
        )
    manager = WorktreeManager(database)
    prepared = manager._prepared(package.package_id)
    if prepared is None:
        raise WorkforceResourceError(
            f"package has no durable prepared worktree: {package.package_id}"
        )
    if prepared.resource_id != requested_resource_id:
        raise WorkforceResourceError(
            "registered resource does not match the durable prepared worktree"
        )
    if not all(
        (
            prepared.repository_common_dir,
            prepared.source_git_dir,
            prepared.worktree_git_dir,
        )
    ):
        raise WorkforceResourceError(
            "prepared worktree lacks durable linked Git identity"
        )
    expected = Path(package.worktree_path).expanduser().resolve(strict=False)
    observed = Path(str(resource["resolved_root"])).expanduser().resolve(
        strict=False
    )
    if _path_identity(observed) != _path_identity(expected):
        raise WorkforceResourceError(
            "registered resource root does not match the immutable "
            "WorkPackage worktree"
        )
    if not expected.is_dir():
        raise WorkforceResourceError(
            f"immutable WorkPackage worktree is unavailable: {expected}"
        )
    manager._verify_existing(prepared, package)
    if phase == "builder_admission":
        head = _git(expected, "rev-parse", "HEAD")
        if head != package.source_sha:
            raise WorkforceResourceError(
                "registered WorkPackage worktree HEAD does not match source SHA"
            )
        if _git(
            expected,
            "status",
            "--porcelain=v1",
            "--ignored=matching",
        ):
            raise WorkforceResourceError(
                "registered WorkPackage worktree is dirty"
            )
    return expected


@dataclass(frozen=True, slots=True)
class _GitWorktreeIdentity:
    top_level: Path
    common_dir: Path
    git_dir: Path

    @property
    def physical_repository_id(self) -> str:
        return hashlib.sha256(
            _path_identity(self.common_dir).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class _RepositoryMutexAuthority:
    lease_key: str
    owner_id: str
    heartbeat_errors: list[BaseException]


def _relative_scope(package: WorkPackage) -> tuple[str, ...]:
    root = Path(package.worktree_path).expanduser().resolve(strict=False)
    normalized: list[str] = []
    for raw in package.allowed_paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise WorkforceResourceError(
                f"allowed path escapes package worktree: {raw}"
            ) from error
        text = PurePosixPath(relative.as_posix()).as_posix().casefold()
        normalized.append(text or ".")
    return tuple(sorted(set(normalized)))


def _path_contains(parent: str, child: str) -> bool:
    if parent == ".":
        return True
    parent_parts = PurePosixPath(parent).parts
    child_parts = PurePosixPath(child).parts
    return len(parent_parts) <= len(child_parts) and (
        child_parts[: len(parent_parts)] == parent_parts
    )


def scopes_overlap(first: WorkPackage, second: WorkPackage) -> bool:
    if first.repository_id != second.repository_id:
        return False
    return _mutable_scopes_overlap(first, second)


def _mutable_scopes_overlap(first: WorkPackage, second: WorkPackage) -> bool:
    return any(
        _path_contains(left, right) or _path_contains(right, left)
        for left in _relative_scope(first)
        for right in _relative_scope(second)
    )


def _comparison_group(package: WorkPackage) -> str:
    return str(package.lineage.get("comparison_group") or "").strip()


def _intentional_overlap(first: WorkPackage, second: WorkPackage) -> bool:
    group = _comparison_group(first)
    return bool(group and group == _comparison_group(second))


@dataclass(frozen=True, slots=True)
class WorktreePreparation:
    package_id: str
    resource_id: str
    source_repository: str
    worktree_path: str
    branch_name: str
    base_sha: str
    claim_id: str
    repository_common_dir: str = ""
    source_git_dir: str = ""
    worktree_git_dir: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "resource_id": self.resource_id,
            "source_repository": self.source_repository,
            "worktree_path": self.worktree_path,
            "branch_name": self.branch_name,
            "base_sha": self.base_sha,
            "claim_id": self.claim_id,
            "repository_common_dir": self.repository_common_dir,
            "source_git_dir": self.source_git_dir,
            "worktree_git_dir": self.worktree_git_dir,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WorktreePreparation":
        return cls(
            package_id=str(payload["package_id"]),
            resource_id=str(payload["resource_id"]),
            source_repository=str(payload["source_repository"]),
            worktree_path=str(payload["worktree_path"]),
            branch_name=str(payload["branch_name"]),
            base_sha=str(payload["base_sha"]),
            claim_id=str(payload["claim_id"]),
            repository_common_dir=str(payload.get("repository_common_dir") or ""),
            source_git_dir=str(payload.get("source_git_dir") or ""),
            worktree_git_dir=str(payload.get("worktree_git_dir") or ""),
        )


class WorktreeManager:
    """Prepare package worktrees without mutating the source checkout."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        controller: str = SUPERVISOR_CONTROLLER,
        source_id: str = "workforce-worktree-manager",
        mutex_wait_seconds: float = _DEFAULT_MUTEX_WAIT_SECONDS,
        mutex_retry_seconds: float = _DEFAULT_MUTEX_RETRY_SECONDS,
        mutex_lease_seconds: int = _DEFAULT_MUTEX_LEASE_SECONDS,
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path).expanduser().resolve() if stop_path else None
        self.controller = controller
        self.source_id = source_id
        self.mutex_wait_seconds = float(mutex_wait_seconds)
        self.mutex_retry_seconds = float(mutex_retry_seconds)
        self.mutex_lease_seconds = int(mutex_lease_seconds)
        if self.mutex_wait_seconds <= 0:
            raise ValueError("mutex_wait_seconds must be positive")
        if self.mutex_retry_seconds <= 0:
            raise ValueError("mutex_retry_seconds must be positive")
        if self.mutex_lease_seconds <= 0:
            raise ValueError("mutex_lease_seconds must be positive")
        self.leases = LeaseManager(database)

    def prepare(
        self,
        package_id: str,
        *,
        source_repository: str | Path,
    ) -> WorktreePreparation:
        state = rebuild_workforce_state(self.database)
        package_state = state.package(package_id)
        package = package_state.package
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        source = Path(source_repository).expanduser().resolve()
        if not source.is_dir():
            raise WorkforceResourceError(
                f"source repository is unavailable: {source}"
            )
        source_identity = self._git_identity(package, source)
        repository = source_identity.top_level
        object_type = self._git_for_package(
            package,
            repository,
            "cat-file",
            "-t",
            package.source_sha,
        )
        if object_type != "commit":
            raise WorkforceResourceError(
                f"package source is not a commit: {package.source_sha}"
            )
        target = Path(package.worktree_path).expanduser()
        if not target.is_absolute():
            raise WorkforceResourceError("worktree_path must be absolute")
        target = target.resolve(strict=False)
        self._require_distinct_target_path(source_identity, target)
        prior = self._prepared(package_id)
        if prior is not None:
            self._verify_existing(
                prior,
                package,
                source_repository=repository,
            )
            if package_state.status == "PLANNED":
                transition_work_package(
                    self.database,
                    package_id,
                    "READY",
                    reason="recovered exact prepared worktree and resource claim",
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                )
            return prior

        resource_id = f"workforce-resource:{package.package_id}"
        repository_key = source_identity.physical_repository_id[:24]
        mutex_key = f"workforce-worktree:{repository_key}"
        mutex_owner = f"{package.package_id}:{uuid.uuid4().hex}"
        self._acquire_mutex(mutex_key, mutex_owner)
        claim: ResourceClaim | None = None
        try:
            if target.exists():
                target_identity = self._verify_partial_worktree(
                    package,
                    target,
                    source_identity=source_identity,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
                target_prepared = True
            else:
                target_identity = None
                target_prepared = False
            if not target_prepared and self._branch_exists_for_package(
                package,
                repository,
                package.branch_name,
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            ):
                branch_head = self._git_for_package(
                    package,
                    repository,
                    "rev-parse",
                    package.branch_name,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
                if branch_head != package.source_sha:
                    raise WorkforceResourceError(
                        "existing package branch does not point at the requested source SHA"
                    )
            claim = self.claim(
                package.package_id,
                resource_id=resource_id,
                source_repository=repository,
            )
            if not target_prepared:
                self._before_external_effect(
                    package,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                if self._branch_exists_for_package(
                    package,
                    repository,
                    package.branch_name,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                ):
                    self._git_for_package(
                        package,
                        repository,
                        "worktree",
                        "add",
                        str(target),
                        package.branch_name,
                        mutex_key=mutex_key,
                        mutex_owner=mutex_owner,
                    )
                else:
                    self._git_for_package(
                        package,
                        repository,
                        "worktree",
                        "add",
                        "-b",
                        package.branch_name,
                        str(target),
                        package.source_sha,
                        mutex_key=mutex_key,
                        mutex_owner=mutex_owner,
                    )
                target_identity = self._git_identity(
                    package,
                    target,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
                self._require_linked_worktree(
                    source_identity,
                    target_identity,
                    expected_target=target,
                    package=package,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
            assert target_identity is not None
            if self._git_for_package(
                package,
                target,
                "rev-parse",
                "HEAD",
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            ) != package.source_sha:
                raise WorkforceResourceError(
                    "prepared worktree HEAD does not match source SHA"
                )
            if self._git_for_package(
                package,
                target,
                "branch",
                "--show-current",
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            ) != package.branch_name:
                raise WorkforceResourceError("prepared worktree has the wrong branch")
            if self._git_for_package(
                package,
                target,
                "status",
                "--porcelain=v1",
                "--ignored=matching",
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            ):
                raise WorkforceResourceError("prepared worktree is not clean")

            self._before_external_effect(
                package,
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            )
            resource = inspect_git_resource(target, resource_id=resource_id)
            self._before_external_effect(
                package,
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            )
            self.database.append(
                registration_event(resource, source_id=self.source_id)
            )
            evidence = {
                "package_id": package.package_id,
                "branch": package.branch_name,
                "base_sha": package.source_sha,
            }
            ready_capabilities = [
                "write_registered_worktree_files",
                "run_registered_local_commands",
                "git_local_commit",
            ]
            from ..fabric.catalog import get_spec

            if get_spec(self.database, package.capability) is not None:
                ready_capabilities.append(package.capability)
            for capability in dict.fromkeys(ready_capabilities):
                self._before_external_effect(
                    package,
                    mutex_key=mutex_key,
                    mutex_owner=mutex_owner,
                )
                set_capability_state(
                    self.database,
                    resource_id,
                    capability,
                    "READY",
                    source_id=self.source_id,
                    evidence=evidence,
                )
            prepared = WorktreePreparation(
                package_id=package.package_id,
                resource_id=resource_id,
                source_repository=str(repository),
                worktree_path=str(target),
                branch_name=package.branch_name,
                base_sha=package.source_sha,
                claim_id=claim.claim_id,
                repository_common_dir=str(source_identity.common_dir),
                source_git_dir=str(source_identity.git_dir),
                worktree_git_dir=str(target_identity.git_dir),
            )
            self._before_external_effect(
                package,
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            )
            _append(
                self.database,
                "workforce.worktree_prepared",
                prepared.to_payload(),
                key=f"workforce.worktree_prepared:{package.package_id}",
                correlation_id=package.package_id,
                source_id=self.source_id,
            )
            transition_work_package(
                self.database,
                package.package_id,
                "READY",
                reason="exact source worktree registered and mutable scope claimed",
                controller=self.controller,
                stop_path=self.stop_path,
                source_id=self.source_id,
            )
            return prepared
        except BaseException as error:
            if claim is not None:
                prepared = self._prepared(package.package_id)
                if prepared is None or prepared.claim_id != claim.claim_id:
                    try:
                        self.release(
                            claim.claim_id,
                            reason=(
                                "worktree preparation failed before durable "
                                f"completion: {type(error).__name__}"
                            ),
                        )
                    except BaseException as recovery_error:
                        raise WorkforceResourceError(
                            "worktree preparation failed and exact claim "
                            f"{claim.claim_id} could not be released: "
                            f"{type(recovery_error).__name__}: {recovery_error}"
                        ) from error
            raise
        finally:
            self.leases.release(mutex_key, mutex_owner)

    def claim(
        self,
        package_id: str,
        *,
        resource_id: str | None = None,
        session_id: str | None = None,
        source_repository: str | Path | None = None,
    ) -> ResourceClaim:
        initial = rebuild_workforce_state(self.database)
        package = initial.package(package_id).package
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        if source_repository is None:
            physical_repository = self._repository_identity_for_package(package)
        else:
            prepared = self._prepared(package.package_id)
            physical_repository = (
                self._verify_existing(
                    prepared,
                    package,
                    source_repository=source_repository,
                )
                if prepared is not None
                else self._git_identity(
                    package,
                    Path(source_repository).expanduser().resolve(),
                )
            )
        physical_repository_id = physical_repository.physical_repository_id
        mutex_key = f"workforce-claim:{physical_repository_id[:24]}"
        mutex_owner = f"{package.package_id}:{uuid.uuid4().hex}"
        self._acquire_mutex(mutex_key, mutex_owner)
        try:
            (
                mutex_authority,
                heartbeat_stop,
                heartbeat_thread,
            ) = self._start_repository_mutex_heartbeat(
                mutex_key,
                mutex_owner,
            )
        except BaseException:
            self.leases.release(mutex_key, mutex_owner)
            raise
        try:
            self.expire_claims(now=utc_now())
            state = rebuild_workforce_state(self.database)
            package_state = state.package(package_id)
            package = package_state.package
            self._require_dependencies(state, package)
            requested_worktree_id = _path_identity(package.worktree_path)
            for existing in state.active_claims():
                existing_repository_id = self._claim_physical_repository_id(
                    existing
                )
                if existing.package_id == package.package_id:
                    if existing_repository_id != physical_repository_id:
                        raise WorkforceResourceError(
                            "active package claim belongs to another physical "
                            "Git repository"
                        )
                    if (
                        _path_identity(existing.worktree_path)
                        != requested_worktree_id
                    ):
                        raise WorkforceResourceError(
                            "active package claim belongs to another physical "
                            "worktree"
                        )
                    return existing
                if (
                    _path_identity(existing.worktree_path)
                    == requested_worktree_id
                ):
                    raise WorkforceResourceError(
                        "physical worktree is already owned by active package "
                        f"{existing.package_id}"
                    )
                if existing_repository_id != physical_repository_id:
                    continue
                other = state.package(existing.package_id).package
                if _mutable_scopes_overlap(
                    package,
                    other,
                ) and not _intentional_overlap(package, other):
                    raise WorkforceResourceError(
                        "mutable scope conflicts with active package "
                        f"{existing.package_id}"
                    )
            generation = 1 + sum(
                1 for claim in state.claims.values()
                if claim.package_id == package.package_id
            )
            claim = ResourceClaim(
                claim_id=f"claim:{package.package_id}:g{generation}",
                package_id=package.package_id,
                session_id=session_id,
                resource_id=resource_id or f"workforce-resource:{package.package_id}",
                worktree_path=str(
                    Path(package.worktree_path).expanduser().resolve(strict=False)
                ),
                mutable_paths=_relative_scope(package),
                lease_expires_at=isoformat(
                    utc_now() + timedelta(seconds=package.lease_seconds)
                ),
            )
            payload = {
                **claim.to_payload(),
                "comparison_group": _comparison_group(package) or None,
                "intentional_overlap": bool(_comparison_group(package)),
                "physical_repository_id": physical_repository_id,
                "repository_common_dir": str(physical_repository.common_dir),
                "expected_version": 0,
                "version": 1,
            }
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
            event = _event(
                "workforce.resource_claimed",
                payload,
                key=f"workforce.resource_claimed:{claim.claim_id}",
                correlation_id=package.package_id,
                source_id=self.source_id,
            )
            with self.database.transaction() as connection:
                self._assert_repository_mutex_authority(
                    mutex_authority,
                    connection=connection,
                )
                self.database.append_events_on_connection(
                    connection,
                    [event],
                )
            return claim
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            self.leases.release(mutex_key, mutex_owner)

    def _claim_release_blocker_on_connection(
        self,
        connection,
        state,
        claim: ResourceClaim,
    ) -> str | None:
        sessions = state.sessions_for(claim.package_id)
        for session in sessions:
            owns_claim = (
                claim.session_id in {session.session_id, session.run_id}
                or str(session.result.get("claim_id") or "") == claim.claim_id
                or str(session.result.get("resource_id") or "")
                == claim.resource_id
            )
            if owns_claim and session.status not in TERMINAL_SESSION_STATUSES:
                return (
                    "nonterminal workforce session can still write: "
                    f"{session.run_id}:{session.status}"
                )

        lease = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            (f"workforce-resource:{claim.resource_id}",),
        ).fetchone()
        if lease is None:
            return None
        try:
            lease_is_live = parse(str(lease["expires_at"])) > utc_now()
        except (TypeError, ValueError):
            return "mutable-resource lease has an invalid expiration"
        if not lease_is_live:
            return None
        owner_id = str(lease["owner_id"])
        owner_session = next(
            (
                session
                for session in sessions
                if owner_id in {session.session_id, session.run_id}
            ),
            None,
        )
        if (
            owner_session is not None
            and owner_session.status in TERMINAL_SESSION_STATUSES
        ):
            return None
        return f"live mutable-resource lease is still owned by {owner_id}"

    def _assert_claim_releasable_on_connection(
        self,
        connection,
        state,
        claim: ResourceClaim,
        *,
        action: str,
    ) -> None:
        blocker = self._claim_release_blocker_on_connection(
            connection,
            state,
            claim,
        )
        if blocker is not None:
            raise WorkforceResourceError(
                f"resource claim cannot be {action} while {blocker}"
            )

    def release(
        self,
        claim_id: str,
        *,
        reason: str,
    ) -> ResourceClaim:
        with self.database.transaction() as connection:
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            try:
                claim = state.claims[claim_id]
            except KeyError as error:
                raise WorkforceResourceError(
                    f"unknown resource claim: {claim_id}"
                ) from error
            if claim.status in {"RELEASED", "EXPIRED"}:
                return claim
            package = state.package(claim.package_id).package
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
                allow_stop=True,
            )
            self._assert_claim_releasable_on_connection(
                connection,
                state,
                claim,
                action="released",
            )
            expected_version = self._claim_version_on_connection(
                connection,
                claim,
            )
            released = ResourceClaim(
                claim_id=claim.claim_id,
                package_id=claim.package_id,
                session_id=claim.session_id,
                resource_id=claim.resource_id,
                worktree_path=claim.worktree_path,
                mutable_paths=claim.mutable_paths,
                lease_expires_at=claim.lease_expires_at,
                status="RELEASED",
            )
            event = _event(
                "workforce.resource_released",
                {
                    **released.to_payload(),
                    "reason": str(reason),
                    "expected_status": claim.status,
                    "expected_version": expected_version,
                    "version": expected_version + 1,
                },
                key=(
                    f"workforce.resource_released:{claim.claim_id}:"
                    f"v{expected_version + 1}"
                ),
                correlation_id=claim.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(connection, [event])
        return released

    def renew(
        self,
        claim_id: str,
        *,
        session_id: str,
        duration_seconds: int | None = None,
        now=None,
    ) -> ResourceClaim:
        """Renew one exact mutable-scope claim for its bound session."""
        observed = now or utc_now()
        with self.database.transaction() as connection:
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            try:
                claim = state.claims[claim_id]
            except KeyError as error:
                raise WorkforceResourceError(
                    f"unknown resource claim: {claim_id}"
                ) from error
            if claim.status != "ACTIVE":
                raise WorkforceResourceError(
                    f"resource claim is not active: {claim_id}"
                )
            if claim.session_id not in {None, session_id}:
                raise WorkforceResourceError(
                    "resource claim belongs to another worker session"
                )
            package = state.package(claim.package_id).package
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
            duration = int(duration_seconds or package.lease_seconds)
            if duration <= 0:
                raise WorkforceResourceError(
                    "resource claim renewal duration must be positive"
                )
            lease_expires_at = isoformat(
                observed + timedelta(seconds=duration)
            )
            if (
                claim.session_id == session_id
                and parse(claim.lease_expires_at) >= parse(lease_expires_at)
            ):
                return claim
            expected_version = self._claim_version_on_connection(
                connection,
                claim,
            )
            renewed = ResourceClaim(
                claim_id=claim.claim_id,
                package_id=claim.package_id,
                session_id=session_id,
                resource_id=claim.resource_id,
                worktree_path=claim.worktree_path,
                mutable_paths=claim.mutable_paths,
                lease_expires_at=lease_expires_at,
                status="ACTIVE",
            )
            event = _event(
                "workforce.resource_renewed",
                {
                    **renewed.to_payload(),
                    "expected_status": claim.status,
                    "expected_version": expected_version,
                    "version": expected_version + 1,
                },
                key=(
                    f"workforce.resource_renewed:{claim.claim_id}:"
                    f"v{expected_version + 1}:{renewed.lease_expires_at}"
                ),
                correlation_id=claim.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(connection, [event])
        return renewed

    def expire_claims(
        self,
        *,
        now=None,
        repository_id: str | None = None,
    ) -> tuple[ResourceClaim, ...]:
        current = now or utc_now()
        candidate_ids = tuple(
            claim.claim_id
            for claim in rebuild_workforce_state(self.database).active_claims()
        )
        expired: list[ResourceClaim] = []
        for claim_id in candidate_ids:
            with self.database.transaction() as connection:
                state = _rebuild_workforce_state_on_connection(
                    self.database,
                    connection,
                )
                claim = state.claims.get(claim_id)
                if claim is None or claim.status != "ACTIVE":
                    continue
                package = state.package(claim.package_id).package
                if repository_id and package.repository_id != repository_id:
                    continue
                if parse(claim.lease_expires_at) > current:
                    continue
                if self._claim_release_blocker_on_connection(
                    connection,
                    state,
                    claim,
                ) is not None:
                    continue
                expected_version = self._claim_version_on_connection(
                    connection,
                    claim,
                )
                value = ResourceClaim(
                    claim_id=claim.claim_id,
                    package_id=claim.package_id,
                    session_id=claim.session_id,
                    resource_id=claim.resource_id,
                    worktree_path=claim.worktree_path,
                    mutable_paths=claim.mutable_paths,
                    lease_expires_at=claim.lease_expires_at,
                    status="EXPIRED",
                )
                event = _event(
                    "workforce.resource_expired",
                    {
                        **value.to_payload(),
                        "expected_status": claim.status,
                        "expected_version": expected_version,
                        "version": expected_version + 1,
                    },
                    key=(
                        f"workforce.resource_expired:{claim.claim_id}:"
                        f"v{expected_version + 1}"
                    ),
                    correlation_id=claim.package_id,
                    source_id=self.source_id,
                )
                self.database.append_events_on_connection(connection, [event])
            expired.append(value)
        return tuple(expired)

    def cleanup_eligibility(self, package_id: str) -> dict[str, Any]:
        state = rebuild_workforce_state(self.database)
        package_state = state.package(package_id)
        target = Path(package_state.package.worktree_path).expanduser().resolve(
            strict=False
        )
        active_claims = [
            claim.claim_id
            for claim in state.active_claims()
            if claim.package_id == package_id
        ]
        exists = target.is_dir()
        clean = bool(exists and not _git(target, "status", "--porcelain=v1"))
        eligible = (
            package_state.status in {"ACCEPTED", "REJECTED", "CANCELLED"}
            and not active_claims
            and clean
        )
        return {
            "package_id": package_id,
            "worktree_path": str(target),
            "exists": exists,
            "clean": clean,
            "active_claims": active_claims,
            "package_status": package_state.status,
            "classification": "ELIGIBLE" if eligible else "PRESERVE",
            "deleted": False,
        }

    def _prepared(self, package_id: str) -> WorktreePreparation | None:
        for event in reversed(
            self.database.events(event_types=("workforce.worktree_prepared",))
        ):
            if str(event.payload.get("package_id") or "") == package_id:
                return WorktreePreparation.from_payload(event.payload)
        return None

    @staticmethod
    def _accepted_claim_history(
        events,
        claim_id: str,
    ) -> tuple[ResourceClaim | None, int, str | None]:
        current: ResourceClaim | None = None
        version = 0
        physical_repository_id: str | None = None
        for event in events:
            payload = event.payload
            if str(payload.get("claim_id") or "") != claim_id:
                continue
            candidate = ResourceClaim.from_payload(payload)
            if current is not None and current.status in {"RELEASED", "EXPIRED"}:
                continue
            expected_status = payload.get("expected_status")
            if (
                current is not None
                and expected_status is not None
                and str(expected_status) != current.status
            ):
                continue
            try:
                expected_version = payload.get("expected_version")
                if (
                    expected_version is not None
                    and int(expected_version) != version
                ):
                    continue
                declared_version = payload.get("version")
                next_version = (
                    int(declared_version)
                    if declared_version is not None
                    else version + 1
                )
            except (TypeError, ValueError) as error:
                raise WorkforceResourceError(
                    f"resource claim has an invalid version: {claim_id}"
                ) from error
            if next_version != version + 1:
                continue
            current = candidate
            version = next_version
            declared_repository_id = str(
                payload.get("physical_repository_id") or ""
            ).strip()
            if declared_repository_id:
                if (
                    physical_repository_id is not None
                    and declared_repository_id != physical_repository_id
                ):
                    raise WorkforceResourceError(
                        "resource claim physical repository identity changed: "
                        f"{claim_id}"
                    )
                physical_repository_id = declared_repository_id
        return current, version, physical_repository_id

    def _claim_version_on_connection(
        self,
        connection,
        claim: ResourceClaim,
    ) -> int:
        placeholders = ", ".join("?" for _ in _CLAIM_EVENT_TYPES)
        rows = connection.execute(
            "SELECT * FROM events "
            f"WHERE event_type IN ({placeholders}) AND correlation_id = ? "
            "ORDER BY sequence",
            (*_CLAIM_EVENT_TYPES, claim.package_id),
        ).fetchall()
        current, version, _ = self._accepted_claim_history(
            (self.database._row_to_event(row) for row in rows),
            claim.claim_id,
        )
        if version < 1:
            raise WorkforceResourceError(
                f"resource claim has no durable history: {claim.claim_id}"
            )
        if current != claim:
            raise WorkforceResourceError(
                "resource claim canonical history disagrees with reduced state: "
                f"{claim.claim_id}"
            )
        return version

    def _claim_physical_repository_id(self, claim: ResourceClaim) -> str:
        current, _, recorded_repository_id = self._accepted_claim_history(
            self.database.events(event_types=_CLAIM_EVENT_TYPES),
            claim.claim_id,
        )
        if current != claim:
            raise WorkforceResourceError(
                "resource claim canonical history disagrees with reduced state: "
                f"{claim.claim_id}"
            )
        prepared = self._prepared(claim.package_id)
        if prepared is not None:
            source_identity = self._verify_existing(prepared)
            actual_repository_id = source_identity.physical_repository_id
            if (
                recorded_repository_id is not None
                and recorded_repository_id != actual_repository_id
            ):
                raise WorkforceResourceError(
                    "resource claim physical repository identity no longer "
                    f"matches its prepared worktree: {claim.claim_id}"
                )
            return actual_repository_id
        if recorded_repository_id is None:
            raise WorkforceResourceError(
                "active resource claim lacks canonical physical Git identity: "
                f"{claim.claim_id}"
            )
        return recorded_repository_id

    def _repository_identity_for_package(
        self,
        package: WorkPackage,
    ) -> _GitWorktreeIdentity:
        prepared = self._prepared(package.package_id)
        if prepared is None:
            raise WorkforceResourceError(
                "physical Git identity is unavailable until the package "
                "worktree is prepared"
            )
        return self._verify_existing(prepared, package)

    def _before_external_effect(
        self,
        package: WorkPackage,
        *,
        mutex_key: str | None = None,
        mutex_owner: str | None = None,
    ) -> None:
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        if mutex_key is None or mutex_owner is None:
            return
        try:
            self.leases.renew(
                mutex_key,
                mutex_owner,
                self.mutex_lease_seconds,
            )
        except LeaseUnavailable as error:
            raise WorkforceResourceError(
                f"repository mutex ownership was lost: {mutex_key}"
            ) from error

    def _git_for_package(
        self,
        package: WorkPackage,
        root: Path,
        *args: str,
        mutex_key: str | None = None,
        mutex_owner: str | None = None,
    ) -> str:
        self._before_external_effect(
            package,
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        )
        return _git(root, *args)

    def _git_identity(
        self,
        package: WorkPackage | None,
        root: Path,
        *,
        mutex_key: str | None = None,
        mutex_owner: str | None = None,
    ) -> _GitWorktreeIdentity:
        requested = Path(root).expanduser().resolve()
        if package is None:
            git = lambda *args: _git(requested, *args)
        else:
            git = lambda *args: self._git_for_package(
                package,
                requested,
                *args,
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            )
        top_level = Path(git("rev-parse", "--show-toplevel")).resolve()
        common_dir = _git_path(
            requested,
            git("rev-parse", "--git-common-dir"),
        )
        git_dir = _git_path(
            requested,
            git("rev-parse", "--git-dir"),
        )
        return _GitWorktreeIdentity(
            top_level=top_level,
            common_dir=common_dir,
            git_dir=git_dir,
        )

    @staticmethod
    def _require_distinct_target_path(
        source_identity: _GitWorktreeIdentity,
        target: Path,
    ) -> None:
        if _path_identity(target) == _path_identity(source_identity.top_level):
            raise WorkforceResourceError(
                "package worktree must be a distinct linked Git worktree, "
                "not the source checkout itself"
            )

    def _require_linked_worktree(
        self,
        source_identity: _GitWorktreeIdentity,
        target_identity: _GitWorktreeIdentity,
        *,
        expected_target: Path,
        package: WorkPackage | None = None,
        mutex_key: str | None = None,
        mutex_owner: str | None = None,
    ) -> None:
        if _path_identity(target_identity.top_level) != _path_identity(
            expected_target
        ):
            raise WorkforceResourceError(
                "package path resolves to another Git worktree"
            )
        if _path_identity(target_identity.top_level) == _path_identity(
            source_identity.top_level
        ):
            raise WorkforceResourceError(
                "package worktree must be distinct from the source checkout"
            )
        if _path_identity(target_identity.common_dir) != _path_identity(
            source_identity.common_dir
        ):
            raise WorkforceResourceError(
                "package path is not a linked worktree of the intended "
                "source repository"
            )
        if _path_identity(target_identity.git_dir) in {
            _path_identity(source_identity.git_dir),
            _path_identity(target_identity.common_dir),
        }:
            raise WorkforceResourceError(
                "package path is not a distinct linked Git worktree"
            )
        metadata_root = target_identity.common_dir / "worktrees"
        try:
            target_identity.git_dir.relative_to(metadata_root)
        except ValueError as error:
            raise WorkforceResourceError(
                "package Git metadata is not owned by the source repository's "
                "linked-worktree registry"
            ) from error
        if package is None:
            worktree_listing = _git(
                source_identity.top_level,
                "worktree",
                "list",
                "--porcelain",
            )
        else:
            worktree_listing = self._git_for_package(
                package,
                source_identity.top_level,
                "worktree",
                "list",
                "--porcelain",
                mutex_key=mutex_key,
                mutex_owner=mutex_owner,
            )
        registered = {
            _path_identity(line.removeprefix("worktree "))
            for line in worktree_listing.splitlines()
            if line.startswith("worktree ")
        }
        if _path_identity(target_identity.top_level) not in registered:
            raise WorkforceResourceError(
                "package worktree is not registered by the intended source "
                "repository"
            )

    def _branch_exists_for_package(
        self,
        package: WorkPackage,
        repository: Path,
        branch: str,
        *,
        mutex_key: str | None = None,
        mutex_owner: str | None = None,
    ) -> bool:
        self._before_external_effect(
            package,
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        )
        return _branch_exists(repository, branch)

    def _verify_partial_worktree(
        self,
        package: WorkPackage,
        target: Path,
        *,
        source_identity: _GitWorktreeIdentity,
        mutex_key: str,
        mutex_owner: str,
    ) -> _GitWorktreeIdentity:
        if not target.is_dir():
            raise WorkforceResourceError(
                f"unregistered package worktree path is not a directory: {target}"
            )
        target_identity = self._git_identity(
            package,
            target,
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        )
        self._require_linked_worktree(
            source_identity,
            target_identity,
            expected_target=target,
            package=package,
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        )
        if self._git_for_package(
            package,
            target,
            "branch",
            "--show-current",
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        ) != package.branch_name:
            raise WorkforceResourceError(
                "unregistered package worktree has the wrong branch"
            )
        if self._git_for_package(
            package,
            target,
            "rev-parse",
            "HEAD",
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        ) != package.source_sha:
            raise WorkforceResourceError(
                "unregistered package worktree does not match source SHA"
            )
        if self._git_for_package(
            package,
            target,
            "status",
            "--porcelain=v1",
            "--ignored=matching",
            mutex_key=mutex_key,
            mutex_owner=mutex_owner,
        ):
            raise WorkforceResourceError(
                "unregistered package worktree is dirty and was preserved"
            )
        return target_identity

    def _verify_existing(
        self,
        prepared: WorktreePreparation,
        package: WorkPackage | None = None,
        *,
        source_repository: str | Path | None = None,
    ) -> _GitWorktreeIdentity:
        target = Path(prepared.worktree_path).expanduser().resolve(strict=False)
        if not target.is_dir():
            raise WorkforceResourceError(
                f"prepared worktree is missing: {prepared.worktree_path}"
            )
        if package is not None:
            if _path_identity(package.worktree_path) != _path_identity(target):
                raise WorkforceResourceError(
                    "prepared worktree path disagrees with the work package"
                )
            if package.branch_name != prepared.branch_name:
                raise WorkforceResourceError(
                    "prepared branch disagrees with the work package"
                )
            if package.source_sha != prepared.base_sha:
                raise WorkforceResourceError(
                    "prepared base SHA disagrees with the work package"
                )
        source = Path(
            source_repository or prepared.source_repository
        ).expanduser().resolve()
        source_identity = self._git_identity(package, source)
        if _path_identity(source_identity.top_level) != _path_identity(
            prepared.source_repository
        ):
            raise WorkforceResourceError(
                "prepared source repository identity changed"
            )
        target_identity = self._git_identity(package, target)
        self._require_linked_worktree(
            source_identity,
            target_identity,
            expected_target=target,
            package=package,
        )
        persisted_paths = (
            ("repository common directory", prepared.repository_common_dir,
             source_identity.common_dir),
            ("source Git directory", prepared.source_git_dir,
             source_identity.git_dir),
            ("worktree Git directory", prepared.worktree_git_dir,
             target_identity.git_dir),
        )
        for name, expected, observed in persisted_paths:
            if expected and _path_identity(expected) != _path_identity(observed):
                raise WorkforceResourceError(
                    f"prepared {name} identity changed"
                )
        if package is None:
            git = lambda *args: _git(target, *args)
        else:
            git = lambda *args: self._git_for_package(package, target, *args)
        if git("branch", "--show-current") != prepared.branch_name:
            raise WorkforceResourceError("prepared worktree branch identity changed")
        try:
            git("cat-file", "-e", f"{prepared.base_sha}^{{commit}}")
        except WorkforceResourceError as error:
            raise WorkforceResourceError(
                "prepared worktree base commit is unavailable"
            ) from error
        head = git("rev-parse", "HEAD")
        try:
            git(
                "merge-base",
                "--is-ancestor",
                prepared.base_sha,
                head,
            )
        except WorkforceResourceError as error:
            raise WorkforceResourceError(
                "prepared worktree base SHA is no longer an ancestor of HEAD"
            ) from error
        return source_identity

    @staticmethod
    def _require_dependencies(state, package: WorkPackage) -> None:
        incomplete = [
            dependency
            for dependency in package.dependencies
            if state.package(dependency).status != "ACCEPTED"
        ]
        if incomplete:
            raise WorkforceResourceError(
                f"package dependencies are not accepted: {incomplete}"
            )

    def _acquire_mutex(self, lease_key: str, owner_id: str) -> None:
        deadline = time.monotonic() + self.mutex_wait_seconds
        while True:
            try:
                self.leases.acquire(
                    lease_key,
                    owner_id,
                    self.mutex_lease_seconds,
                )
                return
            except LeaseUnavailable:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkforceResourceError(
                        "resource claim mutex remained busy for "
                        f"{self.mutex_wait_seconds:.3f}s: {lease_key}"
                    )
                time.sleep(min(self.mutex_retry_seconds, remaining))

    def _start_repository_mutex_heartbeat(
        self,
        lease_key: str,
        owner_id: str,
    ) -> tuple[
        _RepositoryMutexAuthority,
        threading.Event,
        threading.Thread,
    ]:
        stop = threading.Event()
        errors: list[BaseException] = []
        authority = _RepositoryMutexAuthority(
            lease_key=lease_key,
            owner_id=owner_id,
            heartbeat_errors=errors,
        )

        def heartbeat() -> None:
            interval = max(
                0.05,
                min(10.0, self.mutex_lease_seconds / 3),
            )
            while not stop.wait(interval):
                try:
                    self.leases.renew(
                        lease_key,
                        owner_id,
                        self.mutex_lease_seconds,
                    )
                except BaseException as error:
                    errors.append(error)
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"workforce-repository-claim:{lease_key}",
            daemon=True,
        )
        thread.start()
        return authority, stop, thread

    def _assert_repository_mutex_authority(
        self,
        authority: _RepositoryMutexAuthority,
        *,
        connection,
    ) -> None:
        if authority.heartbeat_errors:
            raise WorkforceResourceError(
                "repository claim mutex heartbeat failed before durable append"
            ) from authority.heartbeat_errors[0]
        row = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            (authority.lease_key,),
        ).fetchone()
        try:
            lease_is_live = (
                row is not None
                and parse(str(row["expires_at"])) > utc_now()
            )
        except (TypeError, ValueError):
            lease_is_live = False
        if (
            row is None
            or str(row["owner_id"]) != authority.owner_id
            or not lease_is_live
        ):
            raise WorkforceResourceError(
                "repository claim mutex authority was lost before durable append"
            )


__all__ = [
    "WorkforceResourceError",
    "WorktreeManager",
    "WorktreePreparation",
    "scopes_overlap",
]
