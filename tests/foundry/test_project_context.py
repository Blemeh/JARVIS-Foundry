"""V11 project-context contract, lifecycle, stack, and safety proof."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis_foundry.project_context import (
    ProjectContextResolver,
    ProjectInspectionError,
    ProjectProfileIdentityError,
    ProjectProfileService,
    ProjectProfileStore,
    ProjectProfileStoreError,
    ProjectTarget,
)

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Project Context Test")
    _git(root, "config", "user.email", "project-context@example.invalid")
    for relative, content in files.items():
        _write(root, relative, content)
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "fixture baseline")
    _git(root, "branch", "-M", "main")
    return root


def _mixed_files() -> dict[str, str]:
    return {
        "AGENTS.md": (
            "# Project constraints\n\n"
            "- Never deploy from an unverified branch.\n"
            "- Tests must pass before release.\n"
        ),
        "docs/architecture.md": (
            "# Architecture\n\n"
            "The API owns durable records and the web app is a read client.\n"
            "Components communicate through versioned HTTP contracts.\n"
        ),
        "backend/pyproject.toml": (
            "[build-system]\n"
            "requires = ['hatchling>=1.0']\n"
            "build-backend = 'hatchling.build'\n\n"
            "[project]\n"
            "name = 'owner-api'\n"
            "requires-python = '>=3.12,<3.13'\n"
            "dependencies = ['fastapi>=0.115', 'sqlalchemy>=2', 'pytest>=9', "
            "'ruff>=0.8', 'mypy>=1.11']\n\n"
            "[tool.pytest.ini_options]\n"
            "testpaths = ['tests']\n\n"
            "[tool.ruff]\n"
            "line-length = 100\n\n"
            "[tool.mypy]\n"
            "files = ['app']\n"
        ),
        "backend/uv.lock": "version = 1\n",
        "backend/app/main.py": "VALUE = 1\n",
        "backend/tests/test_main.py": "def test_value(): assert True\n",
        "frontend/package.json": json.dumps(
            {
                "name": "owner-web",
                "packageManager": "pnpm@11.15.1",
                "engines": {"node": ">=22"},
                "scripts": {
                    "build": "next build",
                    "test": "vitest run",
                    "lint": "eslint .",
                    "typecheck": "tsc --noEmit",
                },
                "dependencies": {"next": "16.2.0", "react": "19.2.0"},
                "devDependencies": {
                    "typescript": "5.9.0",
                    "vitest": "4.1.0",
                },
            }
        ),
        "frontend/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "frontend/tsconfig.json": "{}\n",
        "frontend/src/app.tsx": "export const app = true;\n",
        ".github/workflows/ci.yml": "name: CI\non: [push]\njobs: {}\n",
        "Dockerfile": "FROM python:3.12-slim\n",
    }


@pytest.fixture()
def mixed_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "owner-project", _mixed_files())


def _service(tmp_path: Path) -> ProjectProfileService:
    return ProjectProfileService(tmp_path / "profile-cache", clock=lambda: NOW)


def _names(section: dict) -> set[str]:
    return {str(item["name"]) for item in section["values"]}


def _commands(profile, category: str) -> set[tuple[str, ...]]:
    return {tuple(item["argv"]) for item in profile.commands[category]["items"]}


def test_mixed_profile_discovers_stacks_commands_ci_release_and_docs(
    tmp_path: Path,
    mixed_repo: Path,
):
    target = ProjectTarget("owner-project", mixed_repo)
    result = _service(tmp_path).get_profile(target)
    profile = result.profile

    assert result.lifecycle == "FIRST_USE"
    assert result.cache_written is True
    assert profile.stack["classification"] == "MIXED"
    assert {"Python", "JavaScript", "TypeScript"} <= _names(profile.stack["languages"])
    assert {"FastAPI", "SQLAlchemy", "Next.js", "React", "Vitest"} <= _names(
        profile.stack["frameworks"]
    )
    managers = {
        (item["name"], item["ecosystem"])
        for item in profile.stack["package_managers"]["values"]
    }
    assert {("uv", "python"), ("pnpm", "node")} <= managers
    assert ("uv", "build") in _commands(profile, "build")
    assert ("pnpm", "run", "build") in _commands(profile, "build")
    assert ("uv", "run", "pytest", "tests") in _commands(profile, "test")
    assert ("uv", "run", "ruff", "check", ".") in _commands(profile, "lint")
    assert ("uv", "run", "mypy", "app") in _commands(profile, "typecheck")
    assert profile.ci_context["providers"] == ["github_actions"]
    assert profile.release_context["status"] == "KNOWN"
    assert "backend/app" in profile.source_roots
    assert "frontend/src" in profile.source_roots
    assert profile.architecture_hints
    assert profile.known_constraints
    assert profile.recent_relevant_changes
    assert profile.repository_state["dirty"] is False
    assert profile.repository_state["head"] == _git(mixed_repo, "rev-parse", "HEAD")
    assert profile.repository_state["worktrees"]["count"] >= 1
    assert any(
        item["capability"] == "package_manager.pnpm"
        for item in profile.capability_requirements
    )
    assert all(
        item["execute"] is False
        for category in profile.commands.values()
        for item in category["items"]
    )


def test_lifecycle_stale_refresh_and_unchanged_replay_do_not_rewrite(
    tmp_path: Path,
    mixed_repo: Path,
):
    service = _service(tmp_path)
    target = ProjectTarget("owner-project", mixed_repo)
    assert service.status(target).state == "MISSING"
    first = service.get_profile(target)
    path = service.store.profile_path(first.profile.profile_id)
    original_bytes = path.read_bytes()
    original_mtime = path.stat().st_mtime_ns

    replay = service.get_profile(target)
    assert replay.lifecycle == "UNCHANGED_REPLAY"
    assert replay.cache_written is False
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mtime_ns == original_mtime
    assert service.status(target).state == "CURRENT"

    # A source-only edit changes live dirty truth but not the stable profile.
    _write(mixed_repo, "backend/app/main.py", "VALUE = 2\n")
    source_only = service.get_profile(target)
    assert source_only.lifecycle == "UNCHANGED_REPLAY"
    assert source_only.profile.repository_state["dirty"] is True
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mtime_ns == original_mtime

    package = mixed_repo / "frontend" / "package.json"
    data = json.loads(package.read_text(encoding="utf-8"))
    data["scripts"]["check:types"] = "tsc --noEmit"
    package.write_text(json.dumps(data), encoding="utf-8")
    stale = service.status(target)
    assert stale.state == "STALE"
    assert stale.stale is True
    assert "frontend/package.json" in stale.changed_inputs
    refreshed = service.get_profile(target)
    assert refreshed.lifecycle == "REFRESHED"
    assert refreshed.cache_written is True
    assert refreshed.stale_before_refresh is True
    assert refreshed.profile.source_fingerprint != first.profile.source_fingerprint
    assert refreshed.profile.profile_hash != first.profile.profile_hash
    assert ("pnpm", "run", "check:types") in _commands(refreshed.profile, "typecheck")


def test_head_only_change_updates_live_state_without_profile_rewrite(
    tmp_path: Path,
    mixed_repo: Path,
):
    service = _service(tmp_path)
    target = ProjectTarget("owner-project", mixed_repo)
    first = service.get_profile(target)
    path = service.store.profile_path(first.profile.profile_id)
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    old_head = first.profile.repository_state["head"]

    _write(mixed_repo, "backend/app/new_module.py", "ENABLED = True\n")
    _git(mixed_repo, "add", "backend/app/new_module.py")
    _git(mixed_repo, "commit", "-q", "-m", "source-only change")
    replay = service.get_profile(target)

    assert replay.lifecycle == "UNCHANGED_REPLAY"
    assert replay.profile.repository_state["head"] != old_head
    assert replay.profile.profile_hash == first.profile.profile_hash
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


def test_missing_information_remains_unknown(tmp_path: Path):
    repo = _init_repo(tmp_path / "minimal", {"README.md": "# Minimal\n"})
    profile = _service(tmp_path).get_profile(ProjectTarget("minimal", repo)).profile

    assert profile.stack["classification"] == "UNKNOWN"
    assert profile.stack["languages"]["status"] == "UNKNOWN"
    assert profile.stack["runtimes"]["status"] == "UNKNOWN"
    assert profile.stack["frameworks"]["status"] == "UNKNOWN"
    assert profile.stack["package_managers"]["status"] == "UNKNOWN"
    assert all(value["status"] == "UNKNOWN" for value in profile.commands.values())
    assert profile.ci_context["status"] == "UNKNOWN"
    assert profile.release_context["status"] == "UNKNOWN"
    assert "commands.build" in profile.unknowns
    assert "stack.runtimes" in profile.unknowns


def test_package_scripts_without_manager_or_runtime_version_are_not_guessed(
    tmp_path: Path,
):
    repo = _init_repo(
        tmp_path / "node-unknown",
        {
            "package.json": json.dumps(
                {
                    "name": "node-unknown",
                    "scripts": {"build": "next build", "test": "vitest run"},
                    "dependencies": {"next": "16"},
                }
            ),
        },
    )
    profile = (
        _service(tmp_path).get_profile(ProjectTarget("node-unknown", repo)).profile
    )

    assert profile.stack["classification"] == "NODE"
    assert profile.stack["package_managers"]["status"] == "UNKNOWN"
    assert profile.stack["runtimes"]["status"] == "PARTIAL"
    assert profile.stack["runtimes"]["values"] == [
        {
            "name": "Node.js",
            "version": "UNKNOWN",
            "evidence": ["file:package.json"],
        }
    ]
    assert profile.commands["build"]["status"] == "UNKNOWN"
    assert profile.commands["test"]["status"] == "UNKNOWN"


def test_uv_optional_dependencies_are_included_in_fresh_test_command(
    tmp_path: Path,
):
    repo = _init_repo(
        tmp_path / "uv-extras",
        {
            "pyproject.toml": (
                "[build-system]\n"
                "requires = ['setuptools>=75']\n"
                "build-backend = 'setuptools.build_meta'\n\n"
                "[project]\n"
                "name = 'uv-extras'\n"
                "requires-python = '>=3.13'\n\n"
                "[project.optional-dependencies]\n"
                "dev = ['pytest>=9']\n"
                "web = ['trafilatura>=2']\n\n"
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n"
            ),
            "uv.lock": "version = 1\n",
            "tests/test_example.py": "def test_example(): assert True\n",
        },
    )
    profile = _service(tmp_path).get_profile(ProjectTarget("uv-extras", repo)).profile

    assert ("uv", "run", "--all-extras", "pytest", "tests") in _commands(
        profile, "test"
    )


def test_same_repository_worktrees_share_profile_identity_but_keep_live_truth(
    tmp_path: Path,
    mixed_repo: Path,
):
    service = _service(tmp_path)
    target = ProjectTarget("owner-project", mixed_repo)
    first = service.get_profile(target)
    profile_path = service.store.profile_path(first.profile.profile_id)
    profile_mtime = profile_path.stat().st_mtime_ns
    other = tmp_path / "owner-project-feature"
    _git(mixed_repo, "worktree", "add", "-q", "-b", "feature/context", str(other))

    second = service.get_profile(ProjectTarget("owner-project", other))
    assert second.profile.profile_id == first.profile.profile_id
    assert second.lifecycle == "UNCHANGED_REPLAY"
    assert Path(second.profile.root) == other.resolve()
    assert second.profile.repository_state["branch"] == "feature/context"
    assert second.profile.repository_state["worktrees"]["count"] >= 2
    assert profile_path.stat().st_mtime_ns == profile_mtime
    assert service.store.bound_profile_id(other) == first.profile.profile_id


def test_candidate_resolver_is_actionable_but_never_admits_or_executes(
    tmp_path: Path,
    mixed_repo: Path,
):
    resolver = ProjectContextResolver(_service(tmp_path))
    target = ProjectTarget("owner-project", mixed_repo)
    context = resolver.resolve(
        {
            "candidate_id": "candidate-1",
            "objective": "Research a safer dependency upgrade",
            "target_project": "owner-project",
        },
        target,
        consumer="A3",
    )

    assert context["authority"] == "ADVISORY_READ_ONLY"
    assert context["execution"] == {
        "allowed": False,
        "commands_executed": False,
        "repository_mutation": False,
        "external_writes": False,
    }
    assert context["admission"]["evaluated"] is False
    assert context["actionable_context"]["commands"]["test"]["status"] == "KNOWN"
    assert context["actionable_context"]["important_manifests"]
    assert context["profile_ref"]["profile_hash"].startswith("sha256:")
    assert resolver.for_discovery({}, target)["consumer"] == "discovery"
    assert resolver.for_research({}, target)["consumer"] == "research"
    assert resolver.for_a3({}, target)["consumer"] == "A3"
    with pytest.raises(ProjectProfileIdentityError, match="candidate targets"):
        resolver.resolve({"target_project": "other"}, target)


def test_profile_reads_do_not_mutate_target_repository(
    tmp_path: Path,
    mixed_repo: Path,
):
    before = _git(mixed_repo, "status", "--porcelain=v1", "-uall")
    profile = (
        _service(tmp_path)
        .get_profile(ProjectTarget("owner-project", mixed_repo))
        .profile
    )
    after = _git(mixed_repo, "status", "--porcelain=v1", "-uall")

    assert before == after == ""
    assert not (mixed_repo / ".jarvis-fs-idempotency.json").exists()
    assert all(
        item["authority"] == "REPOSITORY_DATA_ONLY" for item in profile.input_evidence
    )


def test_scan_excludes_secrets_dependencies_and_unrelated_source(tmp_path: Path):
    repo = _init_repo(
        tmp_path / "bounded",
        {
            "package.json": json.dumps(
                {
                    "name": "bounded",
                    "packageManager": "npm@11",
                    "scripts": {"test": "vitest run"},
                }
            ),
            "package-lock.json": '{"lockfileVersion":3}\n',
            ".env": "TOKEN=do-not-read\n",
            "src/private.py": "SECRET = 'not-a-profile-input'\n",
            "node_modules/decoy/package.json": json.dumps(
                {
                    "name": "decoy",
                    "scripts": {"build": "malicious-command"},
                }
            ),
        },
    )
    profile = _service(tmp_path).get_profile(ProjectTarget("bounded", repo)).profile
    evidence_paths = {item["path"] for item in profile.input_evidence}
    serialized = json.dumps(profile.to_dict())
    assert ".env" not in evidence_paths
    assert "src/private.py" not in evidence_paths
    assert not any(path.startswith("node_modules/") for path in evidence_paths)
    assert "do-not-read" not in serialized
    assert "malicious-command" not in serialized


def test_profile_store_inside_target_is_refused(tmp_path: Path, mixed_repo: Path):
    service = ProjectProfileService(mixed_repo / ".profile-cache")
    with pytest.raises(ProjectProfileStoreError, match="outside"):
        service.get_profile(ProjectTarget("owner-project", mixed_repo))
    assert not (mixed_repo / ".profile-cache").exists()


def test_invalid_or_nested_targets_fail_closed(tmp_path: Path, mixed_repo: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ProjectInspectionError, match="not a Git repository"):
        _service(tmp_path).get_profile(ProjectTarget("plain", plain))
    with pytest.raises(ProjectInspectionError, match="repository root"):
        _service(tmp_path).get_profile(ProjectTarget("nested", mixed_repo / "backend"))


def test_binding_prevents_silent_repository_identity_swap(
    tmp_path: Path,
    mixed_repo: Path,
):
    service = _service(tmp_path)
    first = service.get_profile(ProjectTarget("owner-project", mixed_repo))
    other_id = "project-profile-" + "f" * 24
    with pytest.raises(ProjectProfileIdentityError, match="already bound"):
        service.store.bind(mixed_repo, other_id)
    assert service.store.bound_profile_id(mixed_repo) == first.profile.profile_id


def test_malformed_manifest_degrades_to_unknown(tmp_path: Path):
    repo = _init_repo(
        tmp_path / "malformed",
        {
            "pyproject.toml": "[project\nname = broken\n",
            "package.json": "{not-json",
            "README.md": "# Broken inputs\n",
        },
    )
    profile = _service(tmp_path).get_profile(ProjectTarget("malformed", repo)).profile
    assert "parse.pyproject.toml" in profile.unknowns
    assert "parse.package.json" in profile.unknowns
    assert profile.stack["classification"] == "UNKNOWN"


def test_remote_credentials_are_not_persisted(tmp_path: Path, mixed_repo: Path):
    secret_remote = "https://token-user:SUPERSECRET@example.com/acme/owner.git"
    _git(mixed_repo, "remote", "add", "origin", secret_remote)
    profile = (
        _service(tmp_path)
        .get_profile(ProjectTarget("owner-project", mixed_repo))
        .profile
    )
    serialized = json.dumps(profile.to_dict())
    assert profile.repository_identity["hosted_slug"] == "acme/owner"
    assert "SUPERSECRET" not in serialized
    assert "token-user" not in serialized


def test_changed_authoritative_doc_is_a_refresh_input(
    tmp_path: Path,
    mixed_repo: Path,
):
    _write(mixed_repo, "OWNER_CONTEXT.md", "# Architecture\nInitial boundary.\n")
    _git(mixed_repo, "add", "OWNER_CONTEXT.md")
    _git(mixed_repo, "commit", "-q", "-m", "add owner context")
    target = ProjectTarget(
        "owner-project", mixed_repo, authoritative_docs=("OWNER_CONTEXT.md",)
    )
    service = _service(tmp_path)
    first = service.get_profile(target)
    _write(
        mixed_repo,
        "OWNER_CONTEXT.md",
        "# Architecture\nChanged authoritative boundary.\n",
    )
    status = service.status(target)
    assert status.state == "STALE"
    assert status.changed_inputs == ("OWNER_CONTEXT.md",)
    second = service.get_profile(target)
    assert second.lifecycle == "REFRESHED"
    assert second.profile.profile_hash != first.profile.profile_hash


def test_project_target_bounds_and_store_identity_are_validated(tmp_path: Path):
    with pytest.raises(ValueError, match="project_id"):
        ProjectTarget("bad project id", tmp_path)
    with pytest.raises(ValueError, match="relative paths"):
        ProjectTarget("good", tmp_path, authoritative_docs=("../escape.md",))
    with pytest.raises(ProjectProfileStoreError, match="absolute"):
        ProjectProfileStore("relative-cache")


def test_configuration_only_change_is_reported_as_stale(tmp_path: Path):
    repo = _init_repo(
        tmp_path / "configuration-change",
        {
            "package.json": json.dumps(
                {
                    "name": "configuration-change",
                    "packageManager": "npm@11",
                }
            ),
            "package-lock.json": '{"lockfileVersion":3}\n',
        },
    )
    service = _service(tmp_path)
    first = ProjectTarget("configuration-change", repo, max_depth=1)
    changed = ProjectTarget("configuration-change", repo, max_depth=2)

    service.get_profile(first)
    status = service.status(changed)

    assert status.state == "STALE"
    assert status.changed_inputs == ("$profile_configuration",)
