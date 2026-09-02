"""Durable profile lifecycle and the non-authoritative A3 context resolver."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..control.redaction import redact_text
from .contracts import (
    CONTEXT_SCHEMA,
    ProfileResolution,
    ProfileStatus,
    ProjectProfile,
    ProjectTarget,
)
from .inspector import ProjectInputSnapshot, ProjectInspector


class ProjectProfileStoreError(RuntimeError):
    """Profile cache is unreadable, unsafe, or internally inconsistent."""


class ProjectProfileIdentityError(ProjectProfileStoreError):
    """A configured path or project name conflicts with persisted identity."""


class ProjectProfileProvider(Protocol):
    """Read-only consumer seam for discovery, research, and A3 planning."""

    def get_profile(self, target: ProjectTarget) -> ProfileResolution: ...

    def status(self, target: ProjectTarget) -> ProfileStatus: ...


def _normal_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve(strict=False))).replace("\\", "/")


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class ProjectProfileStore:
    """Atomic JSON store outside target repositories.

    The bindings file is the durable guard that prevents a repository path
    from silently changing identity.  A no-op bind and a byte-identical save
    perform no replacement, preserving file timestamps for replay proof.
    """

    _PROFILE_ID = re.compile(r"^project-profile-[0-9a-f]{24}$")

    def __init__(self, root: str | Path) -> None:
        configured = Path(root).expanduser()
        if not configured.is_absolute():
            raise ProjectProfileStoreError("profile store root must be absolute")
        self.root = configured.resolve(strict=False)
        if self.root == Path(self.root.anchor).resolve():
            raise ProjectProfileStoreError("profile store root is too broad")

    @property
    def profiles_root(self) -> Path:
        return self.root / "profiles"

    @property
    def bindings_path(self) -> Path:
        return self.root / "bindings.json"

    def profile_path(self, profile_id: str) -> Path:
        if not self._PROFILE_ID.fullmatch(profile_id):
            raise ProjectProfileStoreError("invalid profile_id")
        return self.profiles_root / f"{profile_id}.json"

    def load(self, profile_id: str) -> ProjectProfile | None:
        path = self.profile_path(profile_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile = ProjectProfile.from_dict(raw)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            raise ProjectProfileStoreError(
                f"stored profile is unreadable: {profile_id}"
            ) from exc
        if profile.profile_id != profile_id:
            raise ProjectProfileStoreError("stored profile identity mismatch")
        return profile

    def save(self, profile: ProjectProfile) -> bool:
        path = self.profile_path(profile.profile_id)
        payload = _canonical_bytes(profile.to_dict())
        if path.exists():
            try:
                if path.read_bytes() == payload:
                    return False
            except OSError as exc:
                raise ProjectProfileStoreError("cannot compare stored profile") from exc
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(path, payload)
        return True

    def bound_profile_id(self, repository_root: str | Path) -> str | None:
        bindings = self._load_bindings()
        value = bindings.get(_normal_path(repository_root))
        return str(value) if value else None

    def bind(self, repository_root: str | Path, profile_id: str) -> bool:
        self.profile_path(profile_id)  # validates the identity
        key = _normal_path(repository_root)
        bindings = self._load_bindings()
        existing = bindings.get(key)
        if existing is not None and existing != profile_id:
            raise ProjectProfileIdentityError(
                f"repository path is already bound to {existing}; refusing identity swap"
            )
        if existing == profile_id:
            return False
        bindings[key] = profile_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(self.bindings_path, _canonical_bytes(bindings))
        return True

    def _load_bindings(self) -> dict[str, str]:
        if not self.bindings_path.exists():
            return {}
        try:
            value = json.loads(self.bindings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectProfileStoreError("profile bindings are unreadable") from exc
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise ProjectProfileStoreError("profile bindings are malformed")
        return value

    @staticmethod
    def _atomic_replace(path: Path, payload: bytes) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as exc:
            raise ProjectProfileStoreError(
                f"atomic profile write failed: {path}"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


class ProjectProfileService:
    """Compute on first use, refresh on relevant-input drift, otherwise replay."""

    def __init__(
        self,
        store: ProjectProfileStore | str | Path,
        *,
        inspector: ProjectInspector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = (
            store
            if isinstance(store, ProjectProfileStore)
            else ProjectProfileStore(store)
        )
        self.inspector = inspector or ProjectInspector(clock=clock)

    def status(self, target: ProjectTarget) -> ProfileStatus:
        snapshot = self.inspector.snapshot_inputs(target)
        self._assert_store_outside(snapshot.root)
        bound = self.store.bound_profile_id(snapshot.root)
        if bound is not None and bound != snapshot.profile_id:
            return ProfileStatus(
                state="IDENTITY_CONFLICT",
                profile_id=snapshot.profile_id,
                current_fingerprint=snapshot.source_fingerprint,
                reason=f"configured path is durably bound to {bound}",
            )
        stored = self.store.load(snapshot.profile_id)
        if stored is None:
            return ProfileStatus(
                state="MISSING",
                profile_id=snapshot.profile_id,
                current_fingerprint=snapshot.source_fingerprint,
                reason="profile has not been computed",
            )
        if stored.project_id != target.project_id:
            return ProfileStatus(
                state="IDENTITY_CONFLICT",
                profile_id=snapshot.profile_id,
                current_fingerprint=snapshot.source_fingerprint,
                stored_fingerprint=stored.source_fingerprint,
                reason=(
                    f"profile identity belongs to configured project "
                    f"{stored.project_id!r}, not {target.project_id!r}"
                ),
            )
        changed = self._changed_inputs(stored, snapshot)
        if stored.source_fingerprint != snapshot.source_fingerprint:
            return ProfileStatus(
                state="STALE",
                profile_id=snapshot.profile_id,
                current_fingerprint=snapshot.source_fingerprint,
                stored_fingerprint=stored.source_fingerprint,
                changed_inputs=changed,
                reason="relevant manifest/configuration/documentation input changed",
            )
        return ProfileStatus(
            state="CURRENT",
            profile_id=snapshot.profile_id,
            current_fingerprint=snapshot.source_fingerprint,
            stored_fingerprint=stored.source_fingerprint,
        )

    def get_profile(self, target: ProjectTarget) -> ProfileResolution:
        snapshot = self.inspector.snapshot_inputs(target)
        self._assert_store_outside(snapshot.root)
        bound = self.store.bound_profile_id(snapshot.root)
        if bound is not None and bound != snapshot.profile_id:
            raise ProjectProfileIdentityError(
                f"configured path is durably bound to {bound}; "
                f"current repository resolves to {snapshot.profile_id}"
            )
        stored = self.store.load(snapshot.profile_id)
        if stored is not None and stored.project_id != target.project_id:
            raise ProjectProfileIdentityError(
                f"stored profile belongs to {stored.project_id!r}, "
                f"not {target.project_id!r}"
            )
        if (
            stored is not None
            and stored.source_fingerprint == snapshot.source_fingerprint
        ):
            live = self.inspector.repository_state(snapshot.root)
            self.store.bind(snapshot.root, snapshot.profile_id)
            return ProfileResolution(
                profile=stored.with_repository_view(snapshot.root, live),
                lifecycle="UNCHANGED_REPLAY",
                cache_written=False,
            )

        changed = self._changed_inputs(stored, snapshot) if stored else ()
        profile = self.inspector.compile_profile(target, snapshot)
        cache_written = self.store.save(profile)
        self.store.bind(snapshot.root, snapshot.profile_id)
        return ProfileResolution(
            profile=profile,
            lifecycle="FIRST_USE" if stored is None else "REFRESHED",
            cache_written=cache_written,
            stale_before_refresh=stored is not None,
            changed_inputs=changed,
        )

    def _assert_store_outside(self, repository_root: Path) -> None:
        try:
            self.store.root.relative_to(repository_root)
        except ValueError:
            return
        raise ProjectProfileStoreError(
            "profile store must be outside the target repository"
        )

    @staticmethod
    def _changed_inputs(
        stored: ProjectProfile | None,
        current: ProjectInputSnapshot,
    ) -> tuple[str, ...]:
        if stored is None:
            return ()
        before = {
            str(item.get("path")): (
                item.get("content_hash"),
                item.get("status"),
                item.get("size_bytes"),
                item.get("fingerprint_token"),
            )
            for item in stored.input_evidence
        }
        after = {
            item.path: (
                item.content_hash,
                item.status,
                item.size_bytes,
                item.fingerprint_token,
            )
            for item in current.records
        }
        changed = tuple(
            sorted(
                path
                for path in set(before) | set(after)
                if before.get(path) != after.get(path)
            )
        )
        if not changed and stored.source_fingerprint != current.source_fingerprint:
            return ("$profile_configuration",)
        return changed


class ProjectContextResolver:
    """Candidate + configured target -> inert context for downstream planning.

    This resolver deliberately has no database, admission, scoring, mission,
    effect, worker, or command-execution dependency.  The existing Executive
    remains the only authority that may decide whether work is admitted.
    """

    _CONSUMERS = frozenset({"discovery", "research", "A3"})

    def __init__(self, provider: ProjectProfileProvider) -> None:
        self.provider = provider

    def resolve(
        self,
        candidate: Mapping[str, Any],
        target: ProjectTarget,
        *,
        consumer: str = "A3",
    ) -> dict[str, Any]:
        if consumer not in self._CONSUMERS:
            raise ValueError(f"unsupported project-context consumer: {consumer!r}")
        declared_target = str(candidate.get("target_project") or "").strip()
        if declared_target and declared_target != target.project_id:
            raise ProjectProfileIdentityError(
                f"candidate targets {declared_target!r}, not {target.project_id!r}"
            )
        resolution = self.provider.get_profile(target)
        profile = resolution.profile
        candidate_id = str(
            candidate.get("candidate_id") or candidate.get("id") or "UNKNOWN"
        )[:160]
        objective = redact_text(
            str(candidate.get("objective") or candidate.get("proposed_outcome") or "")
        )[:800]
        return {
            "schema": CONTEXT_SCHEMA,
            "consumer": consumer,
            "authority": "ADVISORY_READ_ONLY",
            "candidate": {
                "candidate_id": candidate_id,
                "objective": objective,
                "target_project": target.project_id,
            },
            "profile_ref": {
                "profile_id": profile.profile_id,
                "profile_hash": profile.profile_hash,
                "evidence_hash": profile.evidence_hash,
                "source_fingerprint": profile.source_fingerprint,
                "lifecycle": resolution.lifecycle,
            },
            "actionable_context": {
                "root": profile.root,
                "repository_identity": dict(profile.repository_identity),
                "repository_state": dict(profile.repository_state),
                "stack": dict(profile.stack),
                "important_manifests": [
                    dict(item) for item in profile.important_manifests
                ],
                "commands": dict(profile.commands),
                "source_roots": list(profile.source_roots),
                "documentation_roots": list(profile.documentation_roots),
                "architecture_hints": [
                    dict(item) for item in profile.architecture_hints
                ],
                "ci_context": dict(profile.ci_context),
                "release_context": dict(profile.release_context),
                "known_constraints": [dict(item) for item in profile.known_constraints],
                "recent_relevant_changes": [
                    dict(item) for item in profile.recent_relevant_changes
                ],
                "capability_requirements": [
                    dict(item) for item in profile.capability_requirements
                ],
                "unknowns": list(profile.unknowns),
            },
            "provenance": {
                "profile": [dict(item) for item in profile.provenance],
                "inputs": [dict(item) for item in profile.input_evidence],
            },
            "execution": {
                "allowed": False,
                "commands_executed": False,
                "repository_mutation": False,
                "external_writes": False,
            },
            "admission": {
                "evaluated": False,
                "authority": "EXISTING_EXECUTIVE_OUTSIDE_THIS_CONTRACT",
            },
        }

    def for_discovery(
        self, candidate: Mapping[str, Any], target: ProjectTarget
    ) -> dict[str, Any]:
        return self.resolve(candidate, target, consumer="discovery")

    def for_research(
        self, candidate: Mapping[str, Any], target: ProjectTarget
    ) -> dict[str, Any]:
        return self.resolve(candidate, target, consumer="research")

    def for_a3(
        self, candidate: Mapping[str, Any], target: ProjectTarget
    ) -> dict[str, Any]:
        return self.resolve(candidate, target, consumer="A3")


__all__ = [
    "ProjectContextResolver",
    "ProjectProfileIdentityError",
    "ProjectProfileProvider",
    "ProjectProfileService",
    "ProjectProfileStore",
    "ProjectProfileStoreError",
]
