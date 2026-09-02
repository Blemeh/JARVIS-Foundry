"""Machine-readable contracts for bounded, read-only project context.

The profile is advisory input for discovery, research, and planning.  It does
not admit work, score candidates, execute commands, or mutate a target
repository.  Volatile repository state is intentionally separate from the
stable profile hash so a branch/HEAD observation can be refreshed without
rewriting an otherwise unchanged persisted profile.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PROFILE_SCHEMA = "jarvis.project-profile.v1"
CONTEXT_SCHEMA = "jarvis.actionable-project-context.v1"
UNKNOWN = "UNKNOWN"

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


@dataclass(frozen=True, slots=True)
class ProjectTarget:
    """One explicitly configured repository and its inspection bounds."""

    project_id: str
    root: str | Path
    authoritative_docs: tuple[str, ...] = ()
    max_depth: int = 4
    max_files: int = 160
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 12_000_000

    def __post_init__(self) -> None:
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise ValueError("project_id must be a stable, bounded identity")
        if not str(self.root).strip():
            raise ValueError("project root is required")
        if not 0 <= self.max_depth <= 6:
            raise ValueError("max_depth must be between 0 and 6")
        if not 1 <= self.max_files <= 256:
            raise ValueError("max_files must be between 1 and 256")
        if not 1_024 <= self.max_file_bytes <= 4_000_000:
            raise ValueError("max_file_bytes must be between 1024 and 4000000")
        if not self.max_file_bytes <= self.max_total_bytes <= 32_000_000:
            raise ValueError(
                "max_total_bytes must cover one file and be at most 32000000"
            )
        for value in self.authoritative_docs:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or not value.strip():
                raise ValueError("authoritative_docs must be bounded relative paths")


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    """Stable project intelligence plus a replaceable live Git observation."""

    schema: str
    profile_id: str
    project_id: str
    repository_identity: Mapping[str, Any]
    root: str
    stack: Mapping[str, Any]
    important_manifests: tuple[Mapping[str, Any], ...]
    commands: Mapping[str, Any]
    source_roots: tuple[str, ...]
    documentation_roots: tuple[str, ...]
    repository_state: Mapping[str, Any]
    architecture_hints: tuple[Mapping[str, Any], ...]
    ci_context: Mapping[str, Any]
    release_context: Mapping[str, Any]
    known_constraints: tuple[Mapping[str, Any], ...]
    recent_relevant_changes: tuple[Mapping[str, Any], ...]
    capability_requirements: tuple[Mapping[str, Any], ...]
    unknowns: tuple[str, ...]
    input_evidence: tuple[Mapping[str, Any], ...]
    provenance: tuple[Mapping[str, Any], ...]
    source_fingerprint: str
    evidence_hash: str
    profile_hash: str
    generated_at: str

    def __post_init__(self) -> None:
        if self.schema != PROFILE_SCHEMA:
            raise ValueError(f"unsupported project profile schema: {self.schema!r}")
        for field_name in ("source_fingerprint", "evidence_hash", "profile_hash"):
            value = str(getattr(self, field_name))
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"{field_name} must be a canonical SHA-256 digest")

    def with_repository_view(
        self, root: str | Path, state: Mapping[str, Any]
    ) -> ProjectProfile:
        """Overlay checkout-local truth without changing stable project identity."""
        provenance: list[Mapping[str, Any]] = []
        for raw in self.provenance:
            item = dict(raw)
            if item.get("kind") == "bounded_repository_read":
                item["root"] = str(root)
                item["profile_cache_replay"] = True
            provenance.append(item)
        return replace(
            self,
            root=str(root),
            repository_state=dict(state),
            provenance=tuple(provenance),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "repository_identity": dict(self.repository_identity),
            "root": self.root,
            "stack": dict(self.stack),
            "important_manifests": [dict(item) for item in self.important_manifests],
            "commands": dict(self.commands),
            "source_roots": list(self.source_roots),
            "documentation_roots": list(self.documentation_roots),
            "repository_state": dict(self.repository_state),
            "architecture_hints": [dict(item) for item in self.architecture_hints],
            "ci_context": dict(self.ci_context),
            "release_context": dict(self.release_context),
            "known_constraints": [dict(item) for item in self.known_constraints],
            "recent_relevant_changes": [
                dict(item) for item in self.recent_relevant_changes
            ],
            "capability_requirements": [
                dict(item) for item in self.capability_requirements
            ],
            "unknowns": list(self.unknowns),
            "input_evidence": [dict(item) for item in self.input_evidence],
            "provenance": [dict(item) for item in self.provenance],
            "source_fingerprint": self.source_fingerprint,
            "evidence_hash": self.evidence_hash,
            "profile_hash": self.profile_hash,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProjectProfile:
        return cls(
            schema=str(value["schema"]),
            profile_id=str(value["profile_id"]),
            project_id=str(value["project_id"]),
            repository_identity=dict(value.get("repository_identity") or {}),
            root=str(value["root"]),
            stack=dict(value.get("stack") or {}),
            important_manifests=tuple(value.get("important_manifests") or ()),
            commands=dict(value.get("commands") or {}),
            source_roots=tuple(value.get("source_roots") or ()),
            documentation_roots=tuple(value.get("documentation_roots") or ()),
            repository_state=dict(value.get("repository_state") or {}),
            architecture_hints=tuple(value.get("architecture_hints") or ()),
            ci_context=dict(value.get("ci_context") or {}),
            release_context=dict(value.get("release_context") or {}),
            known_constraints=tuple(value.get("known_constraints") or ()),
            recent_relevant_changes=tuple(value.get("recent_relevant_changes") or ()),
            capability_requirements=tuple(value.get("capability_requirements") or ()),
            unknowns=tuple(str(item) for item in (value.get("unknowns") or ())),
            input_evidence=tuple(value.get("input_evidence") or ()),
            provenance=tuple(value.get("provenance") or ()),
            source_fingerprint=str(value["source_fingerprint"]),
            evidence_hash=str(value["evidence_hash"]),
            profile_hash=str(value["profile_hash"]),
            generated_at=str(value["generated_at"]),
        )


@dataclass(frozen=True, slots=True)
class ProfileStatus:
    state: str
    profile_id: str
    current_fingerprint: str
    stored_fingerprint: str | None = None
    changed_inputs: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def stale(self) -> bool:
        return self.state == "STALE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "profile_id": self.profile_id,
            "current_fingerprint": self.current_fingerprint,
            "stored_fingerprint": self.stored_fingerprint,
            "changed_inputs": list(self.changed_inputs),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    profile: ProjectProfile
    lifecycle: str
    cache_written: bool
    stale_before_refresh: bool = False
    changed_inputs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "lifecycle": self.lifecycle,
            "cache_written": self.cache_written,
            "stale_before_refresh": self.stale_before_refresh,
            "changed_inputs": list(self.changed_inputs),
        }


__all__ = [
    "CONTEXT_SCHEMA",
    "PROFILE_SCHEMA",
    "UNKNOWN",
    "ProfileResolution",
    "ProfileStatus",
    "ProjectProfile",
    "ProjectTarget",
]
