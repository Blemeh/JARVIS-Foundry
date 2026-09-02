"""Bounded read-only compiler for configured Git repositories.

Only allowlisted manifests, configuration, CI, release, and authoritative
documentation files are read.  Repository content is DATA_ONLY.  Commands are
reported as inert argv hints and are never executed by this package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..connectors.filesystem import FilesystemConnector, FilesystemConnectorError
from ..connectors.git import GitConnector, GitConnectorError
from ..control.redaction import redact_text
from ..events import canonical_json
from ..software_intelligence.project_identity import (
    ProjectIdentityError,
    normalize_remote,
)
from .contracts import PROFILE_SCHEMA, UNKNOWN, ProjectProfile, ProjectTarget


class ProjectInspectionError(ValueError):
    """A configured target is invalid, unbounded, or not a Git repository."""


_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        "dist",
        "build",
        "coverage",
        "artifacts",
        "evidence",
    }
)

_MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "pnpm-workspace.yaml",
        "deno.json",
        "deno.jsonc",
    }
)

_CONFIG_NAMES = frozenset(
    {
        "makefile",
        "justfile",
        "taskfile.yml",
        "taskfile.yaml",
        "tox.ini",
        "noxfile.py",
        ".python-version",
        ".node-version",
        ".nvmrc",
        ".pre-commit-config.yaml",
        ".pre-commit-config.yml",
    }
)

_RELEASE_NAMES = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "compose.prod.yml",
        "compose.prod.yaml",
        "fly.toml",
        "vercel.json",
        "netlify.toml",
        "render.yaml",
        "railway.json",
        "cloudbuild.yaml",
        "procfile",
        "helmfile.yaml",
        "release-please-config.json",
    }
)

_DOC_NAMES = frozenset(
    {
        "readme",
        "readme.md",
        "readme.rst",
        "agents.md",
        "architecture.md",
        "architecture.rst",
        "project_state.md",
        "current_state.md",
        "contributing.md",
        "constraints.md",
        "design.md",
    }
)

_FRAMEWORKS = {
    "fastapi": ("FastAPI", "web_framework"),
    "django": ("Django", "web_framework"),
    "flask": ("Flask", "web_framework"),
    "sqlalchemy": ("SQLAlchemy", "data_framework"),
    "alembic": ("Alembic", "migration_framework"),
    "pytest": ("pytest", "test_framework"),
    "playwright": ("Playwright", "browser_framework"),
    "next": ("Next.js", "web_framework"),
    "react": ("React", "ui_framework"),
    "vite": ("Vite", "build_framework"),
    "vitest": ("Vitest", "test_framework"),
    "vue": ("Vue", "ui_framework"),
    "svelte": ("Svelte", "ui_framework"),
    "express": ("Express", "web_framework"),
    "@nestjs/core": ("NestJS", "web_framework"),
    "@playwright/test": ("Playwright", "browser_framework"),
}


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _iso_now(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normal_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).replace("\\", "/")


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(redact_text(str(value or "").replace("\x00", " ").strip()))[:limit]


def _as_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class InputRecord:
    path: str
    kind: str
    size_bytes: int
    content_hash: str | None
    status: str
    content: bytes | None = None
    fingerprint_token: str | None = None

    def evidence(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "status": self.status,
            "fingerprint_token": self.fingerprint_token,
            "authority": "REPOSITORY_DATA_ONLY",
        }

    def text(self) -> str | None:
        if self.content is None:
            return None
        return self.content.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class ProjectInputSnapshot:
    root: Path
    profile_id: str
    repository_identity: Mapping[str, Any]
    records: tuple[InputRecord, ...]
    source_fingerprint: str
    scan_truncated: bool


class ProjectInspector:
    """Read a configured repository without changing it or executing its code."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- public surface -------------------------------------------------

    def snapshot_inputs(self, target: ProjectTarget) -> ProjectInputSnapshot:
        root = self._repository_root(target)
        identity, profile_id = self._identity(root)
        paths, scan_truncated = self._discover_inputs(root, target)
        reader = FilesystemConnector(root)
        records: list[InputRecord] = []
        total_bytes = 0
        for path, kind in paths:
            relative = path.relative_to(root).as_posix()
            try:
                stat = path.stat()
            except OSError:
                records.append(
                    InputRecord(
                        relative,
                        kind,
                        0,
                        None,
                        "UNAVAILABLE",
                        fingerprint_token="unavailable",
                    )
                )
                continue
            if path.is_symlink() or not path.is_file():
                records.append(
                    InputRecord(
                        relative,
                        kind,
                        int(stat.st_size),
                        None,
                        "UNAVAILABLE",
                        fingerprint_token="not_regular_file",
                    )
                )
                continue
            if stat.st_size > target.max_file_bytes:
                token = _sha(
                    {
                        "path": relative,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "state": "too_large",
                    }
                )
                records.append(
                    InputRecord(
                        relative,
                        kind,
                        int(stat.st_size),
                        None,
                        "SKIPPED_TOO_LARGE",
                        fingerprint_token=token,
                    )
                )
                continue
            if total_bytes + stat.st_size > target.max_total_bytes:
                token = _sha(
                    {
                        "path": relative,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "state": "budget_exhausted",
                    }
                )
                records.append(
                    InputRecord(
                        relative,
                        kind,
                        int(stat.st_size),
                        None,
                        "SKIPPED_BUDGET",
                        fingerprint_token=token,
                    )
                )
                continue
            try:
                snapshot = reader.snapshot(relative)
            except (OSError, FilesystemConnectorError):
                records.append(
                    InputRecord(
                        relative,
                        kind,
                        int(stat.st_size),
                        None,
                        "UNAVAILABLE",
                        fingerprint_token="read_failed",
                    )
                )
                continue
            total_bytes += snapshot.size_bytes
            records.append(
                InputRecord(
                    relative,
                    kind,
                    snapshot.size_bytes,
                    snapshot.content_hash,
                    "READ",
                    snapshot.content,
                    snapshot.content_hash,
                )
            )
        fingerprint_value = {
            "contract": PROFILE_SCHEMA,
            "configuration": {
                "project_id": target.project_id,
                "authoritative_docs": sorted(target.authoritative_docs),
                "max_depth": target.max_depth,
                "max_files": target.max_files,
                "max_file_bytes": target.max_file_bytes,
                "max_total_bytes": target.max_total_bytes,
            },
            "inputs": [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "token": item.fingerprint_token,
                }
                for item in records
            ],
            "scan_truncated": scan_truncated,
        }
        return ProjectInputSnapshot(
            root=root,
            profile_id=profile_id,
            repository_identity=identity,
            records=tuple(records),
            source_fingerprint=_sha(fingerprint_value),
            scan_truncated=scan_truncated,
        )

    def repository_state(self, root: Path) -> dict[str, Any]:
        """Return live Git/worktree truth; every command is observation-only."""
        head = self._git_optional(root, "rev-parse", "HEAD")
        branch = self._git_optional(root, "branch", "--show-current")
        status_raw = self._git_optional(
            root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        dirty_paths: list[str] = []
        if status_raw is not None:
            for item in status_raw.split("\x00"):
                if not item:
                    continue
                dirty_paths.append(_safe_text(item, 500))
        worktree_raw = self._git_optional(root, "worktree", "list", "--porcelain")
        worktrees = self._parse_worktrees(worktree_raw or "", root)
        known_parts = sum(value is not None for value in (branch, status_raw))
        return {
            "status": "KNOWN" if known_parts == 2 else "PARTIAL",
            "branch": branch or ("DETACHED" if head else UNKNOWN),
            "head": head or UNKNOWN,
            "dirty": bool(dirty_paths) if status_raw is not None else UNKNOWN,
            "dirty_paths": dirty_paths[:50],
            "dirty_paths_truncated": len(dirty_paths) > 50,
            "worktrees": worktrees,
            "observed_at": _iso_now(self._clock),
            "volatile": True,
            "authority": "LOCAL_GIT_READ_ONLY",
        }

    def compile_profile(
        self,
        target: ProjectTarget,
        snapshot: ProjectInputSnapshot,
    ) -> ProjectProfile:
        state = self.repository_state(snapshot.root)
        analysis = self._analyze(target, snapshot)
        input_evidence = tuple(item.evidence() for item in snapshot.records)
        provenance = (
            {
                "kind": "bounded_repository_read",
                "authority": "LOCAL_READ_ONLY",
                "root": str(snapshot.root),
                "input_count": len(snapshot.records),
                "scan_truncated": snapshot.scan_truncated,
                "bounds": {
                    "max_depth": target.max_depth,
                    "max_files": target.max_files,
                    "max_file_bytes": target.max_file_bytes,
                    "max_total_bytes": target.max_total_bytes,
                },
            },
            {
                "kind": "git_inspection",
                "authority": "LOCAL_GIT_READ_ONLY",
                "commands": [
                    "rev-parse",
                    "remote",
                    "branch",
                    "status",
                    "worktree list",
                    "log",
                ],
            },
            {
                "kind": "repository_content",
                "authority": "DATA_ONLY",
                "note": "profile content cannot admit work or authorize execution",
            },
        )
        evidence_hash = _sha(
            {
                "inputs": input_evidence,
                "identity": snapshot.repository_identity,
            }
        )
        semantic = {
            "schema": PROFILE_SCHEMA,
            "profile_id": snapshot.profile_id,
            "project_id": target.project_id,
            "repository_identity": snapshot.repository_identity,
            **analysis,
            "source_fingerprint": snapshot.source_fingerprint,
            "evidence_hash": evidence_hash,
        }
        profile_hash = _sha(semantic)
        return ProjectProfile(
            schema=PROFILE_SCHEMA,
            profile_id=snapshot.profile_id,
            project_id=target.project_id,
            repository_identity=dict(snapshot.repository_identity),
            root=str(snapshot.root),
            stack=analysis["stack"],
            important_manifests=tuple(analysis["important_manifests"]),
            commands=analysis["commands"],
            source_roots=tuple(analysis["source_roots"]),
            documentation_roots=tuple(analysis["documentation_roots"]),
            repository_state=state,
            architecture_hints=tuple(analysis["architecture_hints"]),
            ci_context=analysis["ci_context"],
            release_context=analysis["release_context"],
            known_constraints=tuple(analysis["known_constraints"]),
            recent_relevant_changes=tuple(analysis["recent_relevant_changes"]),
            capability_requirements=tuple(analysis["capability_requirements"]),
            unknowns=tuple(analysis["unknowns"]),
            input_evidence=input_evidence,
            provenance=provenance,
            source_fingerprint=snapshot.source_fingerprint,
            evidence_hash=evidence_hash,
            profile_hash=profile_hash,
            generated_at=_iso_now(self._clock),
        )

    # -- repository and bounded input discovery ------------------------

    def _repository_root(self, target: ProjectTarget) -> Path:
        configured = Path(target.root).expanduser()
        if not configured.is_absolute():
            raise ProjectInspectionError("configured project root must be absolute")
        root = configured.resolve(strict=False)
        if not root.is_dir() or root == Path(root.anchor).resolve():
            raise ProjectInspectionError(
                "configured project root is unavailable or broad"
            )
        try:
            top = Path(
                GitConnector._run(root, "rev-parse", "--show-toplevel")
            ).resolve()
        except (GitConnectorError, OSError, subprocess.SubprocessError) as exc:
            raise ProjectInspectionError(
                f"configured root is not a Git repository: {root}"
            ) from exc
        if top != root:
            raise ProjectInspectionError(
                f"configured root must be the repository root; resolved {top}"
            )
        return root

    def _identity(self, root: Path) -> tuple[dict[str, Any], str]:
        remotes_raw = self._git_optional(root, "remote", "-v") or ""
        remotes: list[dict[str, str]] = []
        for line in remotes_raw.splitlines():
            if "\t" not in line or not line.rstrip().endswith("(fetch)"):
                continue
            name, rest = line.split("\t", 1)
            raw = rest.rsplit(" ", 1)[0].strip()
            descriptor = self._remote_descriptor(raw)
            descriptor["name"] = _safe_text(name, 80)
            remotes.append(descriptor)
        remotes.sort(
            key=lambda item: (item.get("name") != "origin", item.get("name", ""))
        )
        hosted = next((item["slug"] for item in remotes if item.get("slug")), None)
        common_value = self._git_optional(root, "rev-parse", "--git-common-dir")
        if common_value:
            common_path = Path(common_value)
            if not common_path.is_absolute():
                common_path = root / common_path
            common_key = _normal_path(common_path)
        else:
            common_key = _normal_path(root / ".git")
        canonical_key = f"git:{hosted}" if hosted else f"gitdir:{common_key}"
        profile_id = (
            "project-profile-"
            + hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:24]
        )
        identity = {
            "kind": "git_repository",
            "canonical_key": canonical_key,
            "hosted_slug": hosted or UNKNOWN,
            "remotes": remotes,
            "git_common_dir_hash": _sha(common_key),
            "identity_source": "origin_remote" if hosted else "git_common_dir",
        }
        return identity, profile_id

    @staticmethod
    def _remote_descriptor(raw: str) -> dict[str, str]:
        # Never persist a credential-bearing or local remote URL.  Hosted
        # identities retain only the normalized owner/repository slug.
        is_windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", raw))
        is_local = is_windows_path or raw.startswith(("/", "file://", "."))
        if not is_local:
            try:
                return {"kind": "hosted", "slug": normalize_remote(raw)}
            except ProjectIdentityError:
                pass
        return {"kind": "opaque", "ref_hash": _sha(raw)}

    def _discover_inputs(
        self, root: Path, target: ProjectTarget
    ) -> tuple[list[tuple[Path, str]], bool]:
        explicit = {
            Path(value).as_posix().lower() for value in target.authoritative_docs
        }
        found: dict[str, tuple[Path, str]] = {}
        queue: deque[tuple[Path, int]] = deque([(root, 0)])
        dirs_seen = 0
        candidate_limit = target.max_files * 6
        while queue and dirs_seen < 1_024 and len(found) < candidate_limit:
            directory, depth = queue.popleft()
            dirs_seen += 1
            try:
                entries = sorted(
                    os.scandir(directory), key=lambda item: item.name.lower()
                )
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if (
                        depth < target.max_depth
                        and entry.name.lower() not in _SKIP_DIRS
                    ):
                        queue.append((Path(entry.path), depth + 1))
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                kind = self._classify_input(relative, explicit)
                if kind:
                    found[relative.lower()] = (path, kind)
        # Explicit docs are checked even when they sit at the configured depth
        # boundary, but they still cannot escape the repository.
        for relative in explicit:
            path = (root / Path(relative)).resolve(strict=False)
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file() and not path.is_symlink():
                found[relative] = (path, "documentation")
        priority = {
            "manifest": 0,
            "configuration": 1,
            "ci": 2,
            "release": 3,
            "documentation": 4,
        }
        ordered = sorted(
            found.values(),
            key=lambda item: (
                priority[item[1]],
                len(item[0].relative_to(root).parts),
                item[0].relative_to(root).as_posix().lower(),
            ),
        )
        truncated = len(ordered) > target.max_files or bool(queue)
        return ordered[: target.max_files], truncated

    @staticmethod
    def _classify_input(relative: str, explicit: set[str]) -> str | None:
        low = relative.lower().replace("\\", "/")
        path = Path(low)
        name = path.name
        parts = path.parts
        if low in explicit:
            return "documentation"
        if name in _MANIFEST_NAMES or (
            name.startswith("requirements") and name.endswith(".txt")
        ):
            return "manifest"
        if name in _CONFIG_NAMES or name.startswith(
            ("tsconfig", "next.config.", "vite.config.", "eslint.config.")
        ):
            return "configuration"
        if low.startswith(".github/workflows/") and path.suffix in {".yml", ".yaml"}:
            return "ci"
        if low in {".gitlab-ci.yml", "azure-pipelines.yml", ".circleci/config.yml"}:
            return "ci"
        if (
            name in _RELEASE_NAMES
            or name.startswith("dockerfile")
            or (
                low.startswith(("deploy/", "deployment/", "infra/", "helm/", "charts/"))
                and path.suffix in {".yml", ".yaml", ".toml", ".json"}
            )
            or (
                low.startswith("scripts/")
                and re.match(
                    r"^(install|installer|release|promot|deploy|package)", name
                )
                and path.suffix in {".py", ".ps1", ".sh", ".cmd", ".bat"}
            )
        ):
            return "release"
        if name in _DOC_NAMES and len(parts) <= 3:
            return "documentation"
        if parts and parts[0] == "docs" and path.suffix in {".md", ".rst"}:
            doc_words = (
                "architect",
                "design",
                "current",
                "state",
                "constraint",
                "overview",
            )
            if any(word in low for word in doc_words):
                return "documentation"
        return None

    # -- semantic analysis ---------------------------------------------

    def _analyze(
        self, target: ProjectTarget, snapshot: ProjectInputSnapshot
    ) -> dict[str, Any]:
        languages: dict[str, set[str]] = {}
        runtimes: dict[tuple[str, str], set[str]] = {}
        frameworks: dict[tuple[str, str], set[str]] = {}
        managers: dict[tuple[str, str, str], set[str]] = {}
        commands: dict[str, list[dict[str, Any]]] = {
            "build": [],
            "test": [],
            "lint": [],
            "typecheck": [],
        }
        components: list[dict[str, Any]] = []
        parse_unknowns: list[str] = []
        release_scripts: list[dict[str, Any]] = []
        record_by_path = {item.path.lower(): item for item in snapshot.records}

        def evidence(path: str, suffix: str = "") -> str:
            return f"file:{path}{suffix}"

        for record in snapshot.records:
            text = record.text()
            if text is None:
                if record.status != "READ":
                    parse_unknowns.append(f"input.{record.path}:{record.status}")
                continue
            name = Path(record.path).name.lower()
            cwd = Path(record.path).parent.as_posix()
            if cwd == ".":
                cwd = "."
            if name == "pyproject.toml":
                try:
                    data = tomllib.loads(text)
                except tomllib.TOMLDecodeError:
                    parse_unknowns.append(f"parse.{record.path}")
                    continue
                project = _as_dict(data.get("project"))
                project_name = _safe_text(
                    project.get("name") or Path(cwd).name or target.project_id, 160
                )
                components.append(
                    {
                        "name": project_name,
                        "kind": "python",
                        "root": cwd,
                        "evidence": evidence(record.path, "#[project]"),
                    }
                )
                languages.setdefault("Python", set()).add(evidence(record.path))
                requires_python = _safe_text(project.get("requires-python"), 100)
                if requires_python:
                    runtimes.setdefault(("Python", requires_python), set()).add(
                        evidence(record.path, "#[project].requires-python")
                    )
                else:
                    runtimes.setdefault(("Python", UNKNOWN), set()).add(
                        evidence(record.path, "#[project]")
                    )
                dependencies = self._python_dependencies(data)
                self._add_frameworks(dependencies, record.path, frameworks)
                manager = self._python_manager(record.path, record_by_path)
                if manager:
                    managers.setdefault((manager, "python", UNKNOWN), set()).add(
                        evidence(record.path)
                    )
                self._python_commands(
                    data,
                    record.path,
                    cwd,
                    manager,
                    dependencies,
                    commands,
                    uv_all_extras=(
                        manager == "uv"
                        and bool(_as_dict(project.get("optional-dependencies")))
                    ),
                )
            elif name.startswith("requirements") and name.endswith(".txt"):
                languages.setdefault("Python", set()).add(evidence(record.path))
                runtimes.setdefault(("Python", UNKNOWN), set()).add(
                    evidence(record.path)
                )
                managers.setdefault(("pip", "python", UNKNOWN), set()).add(
                    evidence(record.path)
                )
                dependencies = self._requirements_dependencies(text)
                self._add_frameworks(dependencies, record.path, frameworks)
                components.append(
                    {
                        "name": Path(cwd).name or target.project_id,
                        "kind": "python",
                        "root": cwd,
                        "evidence": evidence(record.path),
                    }
                )
                if "pytest" in dependencies:
                    self._add_command(
                        commands,
                        "test",
                        ["python", "-m", "pytest"],
                        cwd,
                        evidence(record.path, "#pytest"),
                        "DERIVED",
                    )
            elif name == "package.json":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    parse_unknowns.append(f"parse.{record.path}")
                    continue
                if not isinstance(data, dict):
                    parse_unknowns.append(f"parse.{record.path}")
                    continue
                dependencies = {
                    str(key).lower()
                    for field in ("dependencies", "devDependencies", "peerDependencies")
                    for key in _as_dict(data.get(field))
                }
                languages.setdefault("JavaScript", set()).add(evidence(record.path))
                if "typescript" in dependencies or self._has_nearby_tsconfig(
                    record.path, record_by_path
                ):
                    languages.setdefault("TypeScript", set()).add(evidence(record.path))
                node_version = _safe_text(
                    _as_dict(data.get("engines")).get("node"), 100
                )
                if node_version:
                    runtimes.setdefault(("Node.js", node_version), set()).add(
                        evidence(record.path, "#engines.node")
                    )
                else:
                    runtimes.setdefault(("Node.js", UNKNOWN), set()).add(
                        evidence(record.path)
                    )
                self._add_frameworks(dependencies, record.path, frameworks)
                manager, manager_version, manager_ref = self._node_manager(
                    data, record.path, record_by_path
                )
                if manager:
                    managers.setdefault((manager, "node", manager_version), set()).add(
                        manager_ref
                    )
                components.append(
                    {
                        "name": _safe_text(
                            data.get("name") or Path(cwd).name or target.project_id, 160
                        ),
                        "kind": "node",
                        "root": cwd,
                        "evidence": evidence(record.path),
                    }
                )
                scripts = _as_dict(data.get("scripts"))
                for script_name, script_value in sorted(scripts.items()):
                    script = str(script_name).lower()
                    category = self._script_category(script)
                    source = evidence(record.path, f"#scripts.{script_name}")
                    if category and manager:
                        self._add_command(
                            commands,
                            category,
                            [manager, "run", str(script_name)],
                            cwd,
                            source,
                            "EXPLICIT",
                            declared_script=_safe_text(script_value, 500),
                        )
                    if script.startswith(("deploy", "release", "publish")):
                        release_scripts.append(
                            {
                                "script": str(script_name),
                                "cwd": cwd,
                                "command": _safe_text(script_value, 500),
                                "evidence": source,
                                "execute": False,
                            }
                        )
            elif name in {".python-version"}:
                value = _safe_text(
                    text.splitlines()[0] if text.splitlines() else "", 100
                )
                if value:
                    languages.setdefault("Python", set()).add(evidence(record.path))
                    runtimes.setdefault(("Python", value), set()).add(
                        evidence(record.path)
                    )
            elif name in {".node-version", ".nvmrc"}:
                value = _safe_text(
                    text.splitlines()[0] if text.splitlines() else "", 100
                )
                if value:
                    languages.setdefault("JavaScript", set()).add(evidence(record.path))
                    runtimes.setdefault(("Node.js", value), set()).add(
                        evidence(record.path)
                    )
            elif name.startswith("tsconfig"):
                languages.setdefault("TypeScript", set()).add(evidence(record.path))

        self._drop_unknown_versions(runtimes)
        stack_class = self._stack_class(languages)
        command_contract: dict[str, Any] = {}
        unknowns = list(dict.fromkeys(parse_unknowns))
        for category in ("build", "test", "lint", "typecheck"):
            items = commands[category][:16]
            command_contract[category] = {
                "status": "KNOWN" if items else UNKNOWN,
                "items": items,
            }
            if not items:
                unknowns.append(f"commands.{category}")

        architecture, constraints = self._document_hints(snapshot.records)
        if not architecture:
            unknowns.append("architecture_hints")
        if not constraints:
            unknowns.append("known_constraints")
        ci_context = self._ci_context(snapshot.records)
        if ci_context["status"] == UNKNOWN:
            unknowns.append("ci_context")
        release_context = self._release_context(snapshot.records, release_scripts)
        if release_context["status"] == UNKNOWN:
            unknowns.append("release_context")
        recent = self._recent_changes(snapshot.root, snapshot.records)
        if not recent:
            unknowns.append("recent_relevant_changes")

        stack: dict[str, Any] = {
            "classification": stack_class,
            "languages": self._knowledge_values(languages),
            "runtimes": self._typed_values(runtimes, "name", "version"),
            "frameworks": self._typed_values(frameworks, "name", "kind"),
            "package_managers": self._manager_values(managers),
            "components": self._dedupe_dicts(components, ("kind", "root", "name")),
        }
        for key in ("languages", "runtimes", "frameworks", "package_managers"):
            if stack[key]["status"] == UNKNOWN:
                unknowns.append(f"stack.{key}")
            elif stack[key]["status"] == "PARTIAL":
                unknowns.append(f"stack.{key}.details")

        manifests = [
            {
                "path": item.path,
                "kind": item.kind,
                "content_hash": item.content_hash,
                "size_bytes": item.size_bytes,
                "status": item.status,
            }
            for item in snapshot.records
            if item.kind != "documentation"
        ]
        source_roots = self._source_roots(snapshot.root, snapshot.records)
        docs_roots = self._documentation_roots(snapshot.records)
        if not source_roots:
            unknowns.append("source_roots")
        capabilities = self._capability_requirements(stack, ci_context, release_context)
        return {
            "stack": stack,
            "important_manifests": manifests,
            "commands": command_contract,
            "source_roots": source_roots,
            "documentation_roots": docs_roots,
            "architecture_hints": architecture,
            "ci_context": ci_context,
            "release_context": release_context,
            "known_constraints": constraints,
            "recent_relevant_changes": recent,
            "capability_requirements": capabilities,
            "unknowns": sorted(set(unknowns)),
        }

    @staticmethod
    def _python_dependencies(data: Mapping[str, Any]) -> set[str]:
        raw: list[Any] = []
        project = _as_dict(data.get("project"))
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            raw.extend(dependencies)
        for values in _as_dict(project.get("optional-dependencies")).values():
            if isinstance(values, list):
                raw.extend(values)
        for values in _as_dict(data.get("dependency-groups")).values():
            if isinstance(values, list):
                raw.extend(values)
        return {
            re.split(r"[<>=!~;\[\s]", str(value).strip(), maxsplit=1)[0].lower()
            for value in raw
            if str(value).strip()
        }

    @staticmethod
    def _requirements_dependencies(text: str) -> set[str]:
        values: set[str] = set()
        for line in text.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped or stripped.startswith(("-", "http://", "https://")):
                continue
            name = re.split(r"[<>=!~;\[\s]", stripped, maxsplit=1)[0].lower()
            if name:
                values.add(name)
        return values

    @staticmethod
    def _add_frameworks(
        dependencies: Iterable[str],
        source_path: str,
        output: dict[tuple[str, str], set[str]],
    ) -> None:
        for dependency in dependencies:
            mapped = _FRAMEWORKS.get(dependency.lower())
            if mapped:
                output.setdefault(mapped, set()).add(f"file:{source_path}")

    @staticmethod
    def _python_manager(path: str, records: Mapping[str, InputRecord]) -> str | None:
        parent = Path(path).parent
        for ancestor in (parent, *parent.parents):
            prefix = "" if ancestor.as_posix() == "." else ancestor.as_posix() + "/"
            for filename, manager in (
                ("uv.lock", "uv"),
                ("poetry.lock", "poetry"),
                ("pipfile.lock", "pipenv"),
                ("pipfile", "pipenv"),
            ):
                if (prefix + filename).lower() in records:
                    return manager
        return None

    @staticmethod
    def _python_argv(
        manager: str | None,
        executable: str,
        *args: str,
        uv_all_extras: bool = False,
    ) -> list[str]:
        if manager == "uv":
            return [
                "uv",
                "run",
                *(["--all-extras"] if uv_all_extras else []),
                executable,
                *args,
            ]
        if manager == "poetry":
            return ["poetry", "run", executable, *args]
        if manager == "pipenv":
            return ["pipenv", "run", executable, *args]
        if executable == "pytest":
            return ["python", "-m", "pytest", *args]
        if executable == "mypy":
            return ["python", "-m", "mypy", *args]
        if executable == "ruff":
            return ["python", "-m", "ruff", *args]
        return [executable, *args]

    def _python_commands(
        self,
        data: Mapping[str, Any],
        path: str,
        cwd: str,
        manager: str | None,
        dependencies: set[str],
        commands: dict[str, list[dict[str, Any]]],
        *,
        uv_all_extras: bool = False,
    ) -> None:
        tool = _as_dict(data.get("tool"))
        if isinstance(data.get("build-system"), dict):
            argv = (
                ["uv", "build"]
                if manager == "uv"
                else ["poetry", "build"]
                if manager == "poetry"
                else ["python", "-m", "build"]
            )
            self._add_command(
                commands,
                "build",
                argv,
                cwd,
                f"file:{path}#[build-system]",
                "DERIVED",
            )
        jarvis = _as_dict(tool.get("jarvis"))
        jarvis_tests = _as_dict(jarvis.get("tests"))
        for variant, values in sorted(jarvis_tests.items()):
            if isinstance(values, list) and all(
                isinstance(item, str) for item in values
            ):
                self._add_command(
                    commands,
                    "test",
                    self._python_argv(
                        manager,
                        "pytest",
                        *values,
                        uv_all_extras=uv_all_extras,
                    ),
                    cwd,
                    f"file:{path}#[tool.jarvis.tests].{variant}",
                    "EXPLICIT",
                    variant=str(variant),
                )
        pytest_tool = _as_dict(tool.get("pytest"))
        pytest_options = _as_dict(pytest_tool.get("ini_options"))
        testpaths = pytest_options.get("testpaths") or ()
        if isinstance(testpaths, list) and all(
            isinstance(item, str) for item in testpaths
        ):
            self._add_command(
                commands,
                "test",
                self._python_argv(
                    manager,
                    "pytest",
                    *testpaths,
                    uv_all_extras=uv_all_extras,
                ),
                cwd,
                f"file:{path}#[tool.pytest.ini_options].testpaths",
                "DERIVED",
            )
        elif "pytest" in dependencies:
            self._add_command(
                commands,
                "test",
                self._python_argv(
                    manager,
                    "pytest",
                    uv_all_extras=uv_all_extras,
                ),
                cwd,
                f"file:{path}#dependency:pytest",
                "DERIVED",
            )
        if isinstance(tool.get("ruff"), dict) or "ruff" in dependencies:
            self._add_command(
                commands,
                "lint",
                self._python_argv(
                    manager,
                    "ruff",
                    "check",
                    ".",
                    uv_all_extras=uv_all_extras,
                ),
                cwd,
                f"file:{path}#[tool.ruff]",
                "DERIVED",
            )
        mypy = _as_dict(tool.get("mypy"))
        if mypy or "mypy" in dependencies:
            configured_files = mypy.get("files")
            files: list[Any] = (
                configured_files if isinstance(configured_files, list) else ["."]
            )
            self._add_command(
                commands,
                "typecheck",
                self._python_argv(
                    manager,
                    "mypy",
                    *(str(item) for item in files),
                    uv_all_extras=uv_all_extras,
                ),
                cwd,
                f"file:{path}#[tool.mypy]",
                "DERIVED",
            )

    @staticmethod
    def _has_nearby_tsconfig(
        package_path: str, records: Mapping[str, InputRecord]
    ) -> bool:
        parent = Path(package_path).parent.as_posix()
        prefix = "" if parent == "." else parent + "/"
        return any(key.startswith((prefix + "tsconfig").lower()) for key in records)

    @staticmethod
    def _node_manager(
        data: Mapping[str, Any],
        path: str,
        records: Mapping[str, InputRecord],
    ) -> tuple[str | None, str, str]:
        declared = str(data.get("packageManager") or "").strip()
        if declared:
            name, _, version = declared.partition("@")
            if name in {"npm", "pnpm", "yarn", "bun"}:
                return name, version or UNKNOWN, f"file:{path}#packageManager"
        parent = Path(path).parent
        for ancestor in parent.parents:
            prefix = "" if ancestor.as_posix() == "." else ancestor.as_posix() + "/"
            key = (prefix + "package.json").lower()
            inherited = records.get(key)
            if inherited is None:
                continue
            inherited_text = inherited.text()
            if inherited_text is None:
                continue
            try:
                parent_raw = json.loads(inherited_text)
            except json.JSONDecodeError:
                continue
            parent_data = _as_dict(parent_raw)
            parent_manager = str(parent_data.get("packageManager") or "").strip()
            inherited_name, _, inherited_version = parent_manager.partition("@")
            if inherited_name in {"npm", "pnpm", "yarn", "bun"}:
                return (
                    inherited_name,
                    inherited_version or UNKNOWN,
                    f"file:{inherited.path}#packageManager",
                )
        for ancestor in (parent, *parent.parents):
            prefix = "" if ancestor.as_posix() == "." else ancestor.as_posix() + "/"
            for filename, manager in (
                ("pnpm-lock.yaml", "pnpm"),
                ("yarn.lock", "yarn"),
                ("bun.lock", "bun"),
                ("bun.lockb", "bun"),
                ("package-lock.json", "npm"),
            ):
                key = (prefix + filename).lower()
                if key in records:
                    return manager, UNKNOWN, f"file:{records[key].path}"
        return None, UNKNOWN, f"file:{path}#package-manager-unknown"

    @staticmethod
    def _script_category(name: str) -> str | None:
        if name == "build" or name.startswith("build:"):
            return "build"
        if name == "test" or name.startswith("test:") or name == "e2e":
            return "test"
        if name == "lint" or name.startswith("lint:"):
            return "lint"
        if name in {
            "typecheck",
            "type-check",
            "check:types",
            "check-types",
        } or name.startswith("typecheck:"):
            return "typecheck"
        return None

    @staticmethod
    def _add_command(
        commands: dict[str, list[dict[str, Any]]],
        category: str,
        argv: Sequence[str],
        cwd: str,
        source: str,
        confidence: str,
        **metadata: Any,
    ) -> None:
        item = {
            "argv": [str(value) for value in argv],
            "command": subprocess.list2cmdline([str(value) for value in argv]),
            "cwd": cwd,
            "source": source,
            "confidence": confidence,
            "execute": False,
            **metadata,
        }
        key = (tuple(item["argv"]), cwd)
        if not any(
            (tuple(existing["argv"]), existing["cwd"]) == key
            for existing in commands[category]
        ):
            commands[category].append(item)

    @staticmethod
    def _knowledge_values(values: Mapping[str, set[str]]) -> dict[str, Any]:
        items = [
            {"name": name, "evidence": sorted(refs)}
            for name, refs in sorted(values.items())
        ]
        return {"status": "KNOWN" if items else UNKNOWN, "values": items}

    @staticmethod
    def _typed_values(
        values: Mapping[tuple[str, str], set[str]], first: str, second: str
    ) -> dict[str, Any]:
        items = [
            {first: key[0], second: key[1], "evidence": sorted(refs)}
            for key, refs in sorted(values.items())
        ]
        status = (
            UNKNOWN
            if not items
            else "PARTIAL"
            if any(item[second] == UNKNOWN for item in items)
            else "KNOWN"
        )
        return {"status": status, "values": items}

    @staticmethod
    def _manager_values(
        values: Mapping[tuple[str, str, str], set[str]],
    ) -> dict[str, Any]:
        items = [
            {
                "name": key[0],
                "ecosystem": key[1],
                "version": key[2],
                "evidence": sorted(refs),
            }
            for key, refs in sorted(values.items())
        ]
        status = (
            UNKNOWN
            if not items
            else "PARTIAL"
            if any(item["version"] == UNKNOWN for item in items)
            else "KNOWN"
        )
        return {"status": status, "values": items}

    @staticmethod
    def _drop_unknown_versions(
        values: dict[tuple[str, str], set[str]],
    ) -> None:
        names_with_versions = {name for name, version in values if version != UNKNOWN}
        for key in list(values):
            if key[1] == UNKNOWN and key[0] in names_with_versions:
                del values[key]

    @staticmethod
    def _stack_class(languages: Mapping[str, set[str]]) -> str:
        python = "Python" in languages
        node = "JavaScript" in languages or "TypeScript" in languages
        if python and node:
            return "MIXED"
        if python:
            return "PYTHON"
        if node:
            return "NODE"
        return UNKNOWN

    @staticmethod
    def _dedupe_dicts(
        values: Iterable[dict[str, Any]], fields: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for value in values:
            key = tuple(value.get(field) for field in fields)
            if key not in seen:
                seen.add(key)
                output.append(value)
        return output

    def _document_hints(
        self, records: Sequence[InputRecord]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        architecture: list[dict[str, Any]] = []
        constraints: list[dict[str, Any]] = []
        architecture_words = {
            "architecture",
            "design",
            "structure",
            "system",
            "overview",
            "tech stack",
            "project mission",
            "components",
        }
        constraint_words = {
            "constraint",
            "invariant",
            "rule",
            "boundary",
            "requirement",
            "forbidden",
            "do not",
            "must not",
        }
        for record in records:
            if record.kind != "documentation":
                continue
            document_text = record.text()
            if document_text is None:
                continue
            in_architecture = False
            in_constraints = False
            in_code = False
            for number, raw in enumerate(document_text.splitlines(), start=1):
                stripped = raw.strip()
                if stripped.startswith("```"):
                    in_code = not in_code
                    continue
                if in_code:
                    continue
                if stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip().lower()
                    in_architecture = any(
                        word in heading for word in architecture_words
                    )
                    in_constraints = any(word in heading for word in constraint_words)
                    continue
                clean = _safe_text(re.sub(r"^[-*+]\s+", "", stripped), 360)
                if not clean or len(clean) < 12:
                    continue
                ref = f"file:{record.path}#L{number}"
                item = {
                    "text": clean,
                    "evidence": ref,
                    "content_hash": _sha(clean),
                    "authority": "REPOSITORY_DATA_ONLY",
                }
                if in_architecture and len(architecture) < 20:
                    architecture.append(item)
                constraint_signal = in_constraints or (
                    Path(record.path).name.lower() == "agents.md"
                    and re.search(
                        r"\b(must|never|required|forbidden|do not)\b",
                        clean,
                        re.IGNORECASE,
                    )
                )
                if constraint_signal and len(constraints) < 24:
                    constraints.append(item)
        return (
            self._dedupe_dicts(architecture, ("content_hash",)),
            self._dedupe_dicts(constraints, ("content_hash",)),
        )

    @staticmethod
    def _ci_context(records: Sequence[InputRecord]) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        providers: set[str] = set()
        for item in records:
            if item.kind != "ci":
                continue
            low = item.path.lower()
            provider = (
                "github_actions"
                if low.startswith(".github/workflows/")
                else "gitlab_ci"
                if low == ".gitlab-ci.yml"
                else "azure_pipelines"
                if low == "azure-pipelines.yml"
                else "circleci"
                if low == ".circleci/config.yml"
                else UNKNOWN
            )
            providers.add(provider)
            files.append(
                {
                    "path": item.path,
                    "provider": provider,
                    "content_hash": item.content_hash,
                    "status": item.status,
                }
            )
        return {
            "status": "KNOWN" if files else UNKNOWN,
            "providers": sorted(providers),
            "files": files,
        }

    @staticmethod
    def _release_context(
        records: Sequence[InputRecord], scripts: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        hints: list[dict[str, Any]] = []
        for item in records:
            if item.kind != "release":
                continue
            low = item.path.lower()
            kind = (
                "container"
                if "dockerfile" in low or "compose" in low
                else "kubernetes"
                if low.startswith(("helm/", "charts/"))
                else "deployment_config"
            )
            hints.append(
                {
                    "kind": kind,
                    "path": item.path,
                    "content_hash": item.content_hash,
                    "status": item.status,
                }
            )
        hints.extend(dict(item) for item in scripts)
        return {"status": "KNOWN" if hints else UNKNOWN, "hints": hints[:40]}

    def _recent_changes(
        self, root: Path, records: Sequence[InputRecord]
    ) -> list[dict[str, Any]]:
        paths = [item.path for item in records if item.status == "READ"][:80]
        if not paths:
            return []
        raw = self._git_optional(
            root,
            "log",
            "-n",
            "12",
            "--date=iso-strict",
            "--format=%H%x1f%cI%x1f%s%x1e",
            "--",
            *paths,
        )
        if not raw:
            return []
        output: list[dict[str, Any]] = []
        for record in raw.split("\x1e"):
            fields = record.strip().split("\x1f")
            if len(fields) != 3:
                continue
            output.append(
                {
                    "commit": fields[0],
                    "committed_at": fields[1],
                    "subject": _safe_text(fields[2], 300),
                    "relevance": "profile_input_changed",
                }
            )
        return output

    @staticmethod
    def _source_roots(root: Path, records: Sequence[InputRecord]) -> list[str]:
        candidates: set[Path] = set()
        common = {
            "src",
            "app",
            "apps",
            "packages",
            "lib",
            "server",
            "client",
            "services",
        }
        for record in records:
            if Path(record.path).name.lower() not in {
                "pyproject.toml",
                "package.json",
                "setup.py",
                "requirements.txt",
            }:
                continue
            parent = (root / Path(record.path).parent).resolve(strict=False)
            for name in common:
                candidate = parent / name
                if candidate.is_dir() and not candidate.is_symlink():
                    candidates.add(candidate)
            # Requirements-only Python components commonly place source files
            # directly below the component root.  Record that root as evidence,
            # not as a guessed framework layout.
            if Path(record.path).name.lower().startswith("requirements"):
                candidates.add(parent)
        for name in ("backend", "frontend"):
            candidate = root / name
            if candidate.is_dir() and not candidate.is_symlink():
                candidates.add(candidate)
        return sorted(
            "." if path == root else path.relative_to(root).as_posix()
            for path in candidates
        )

    @staticmethod
    def _documentation_roots(records: Sequence[InputRecord]) -> list[str]:
        roots = {
            Path(item.path).parent.as_posix()
            for item in records
            if item.kind == "documentation"
        }
        return sorted("." if value == "." else value for value in roots)

    @staticmethod
    def _capability_requirements(
        stack: Mapping[str, Any], ci: Mapping[str, Any], release: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        requirements: dict[str, set[str]] = {
            "git.read": {"repository identity and live state"},
            "filesystem.read": {"bounded manifests and authoritative docs"},
        }
        for item in stack["runtimes"]["values"]:
            name = str(item["name"]).lower().replace(".js", "").replace(" ", "")
            requirements.setdefault(f"runtime.{name}", set()).update(item["evidence"])
        for item in stack["package_managers"]["values"]:
            requirements.setdefault(f"package_manager.{item['name']}", set()).update(
                item["evidence"]
            )
        if ci.get("status") == "KNOWN":
            requirements["ci.read"] = {"configured CI metadata"}
        if any(hint.get("kind") == "container" for hint in release.get("hints", ())):
            requirements["container.read"] = {"Docker/Compose configuration present"}
        return [
            {
                "capability": name,
                "status": "REQUIRED",
                "evidence": sorted(refs),
                "admission_effect": "NONE",
            }
            for name, refs in sorted(requirements.items())
        ]

    # -- Git helpers ----------------------------------------------------

    @staticmethod
    def _git_optional(root: Path, *args: str) -> str | None:
        try:
            return cast(str, GitConnector._run(root, *args))
        except (GitConnectorError, OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _parse_worktrees(raw: str, current_root: Path) -> dict[str, Any]:
        parsed: list[dict[str, Any]] = []
        block: dict[str, Any] = {}
        for line in [*raw.splitlines(), ""]:
            if not line:
                if block:
                    path_value = str(block.get("path") or "")
                    block["is_current"] = bool(path_value) and (
                        _normal_path(Path(path_value)) == _normal_path(current_root)
                    )
                    parsed.append(block)
                    block = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                block["path"] = value
            elif key == "HEAD":
                block["head"] = value
            elif key == "branch":
                block["branch"] = value.removeprefix("refs/heads/")
            elif key in {"detached", "locked", "prunable"}:
                block[key] = value or True
        current = [item for item in parsed if item.get("is_current")]
        others = [item for item in parsed if not item.get("is_current")]
        bounded = (current + others)[:40]
        return {
            "status": "KNOWN" if raw else UNKNOWN,
            "count": len(parsed),
            "items": bounded,
            "truncated": len(parsed) > len(bounded),
        }


__all__ = [
    "InputRecord",
    "ProjectInputSnapshot",
    "ProjectInspectionError",
    "ProjectInspector",
]
