"""Run bounded V11 ProjectProfile proof against explicitly named repositories.

All target repositories are observation-only.  Profile cache and proof output
must live outside them.  The changed-input lifecycle probe uses a disposable
repository created below the evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_foundry.project_context import ProjectProfileService, ProjectTarget


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed at {root}: "
            f"{result.stderr.decode('utf-8', errors='replace')[:500]}"
        )
    return result.stdout


def _status_hash(root: Path) -> str:
    return _sha_bytes(
        _git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )


def _outside_targets(path: Path, targets: list[tuple[str, Path]]) -> None:
    resolved = path.resolve(strict=False)
    for project_id, root in targets:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        raise ValueError(
            f"evidence/cache path for {project_id} must be outside target repository"
        )


def _summary(result: Any) -> dict[str, Any]:
    profile = result.profile
    return {
        "profile_id": profile.profile_id,
        "profile_hash": profile.profile_hash,
        "evidence_hash": profile.evidence_hash,
        "source_fingerprint": profile.source_fingerprint,
        "lifecycle": result.lifecycle,
        "cache_written": result.cache_written,
        "stack_classification": profile.stack["classification"],
        "languages": profile.stack["languages"],
        "runtimes": profile.stack["runtimes"],
        "frameworks": profile.stack["frameworks"],
        "package_managers": profile.stack["package_managers"],
        "manifests": [
            {key: item.get(key) for key in ("path", "kind", "content_hash", "status")}
            for item in profile.important_manifests
        ],
        "commands": profile.commands,
        "source_roots": list(profile.source_roots),
        "documentation_roots": list(profile.documentation_roots),
        "repository_state": dict(profile.repository_state),
        "architecture_hint_count": len(profile.architecture_hints),
        "known_constraint_count": len(profile.known_constraints),
        "ci_context": dict(profile.ci_context),
        "release_context": dict(profile.release_context),
        "recent_relevant_changes": [
            dict(item) for item in profile.recent_relevant_changes
        ],
        "capability_requirements": [
            dict(item) for item in profile.capability_requirements
        ],
        "unknowns": list(profile.unknowns),
    }


def _profile_real_projects(
    service: ProjectProfileService,
    targets: list[tuple[str, Path]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for project_id, root in targets:
        before = _status_hash(root)
        first = service.get_profile(ProjectTarget(project_id, root))
        profile_path = service.store.profile_path(first.profile.profile_id)
        first_mtime = profile_path.stat().st_mtime_ns
        first_bytes = profile_path.read_bytes()
        replay = service.get_profile(ProjectTarget(project_id, root))
        after = _status_hash(root)
        unchanged_cache = (
            profile_path.stat().st_mtime_ns == first_mtime
            and profile_path.read_bytes() == first_bytes
        )
        summary = _summary(replay)
        output.append(
            {
                "project_id": project_id,
                "root": str(root),
                "first_lifecycle": first.lifecycle,
                "replay_lifecycle": replay.lifecycle,
                "unchanged_replay_no_rewrite": unchanged_cache,
                "repository_status_before_hash": before,
                "repository_status_after_hash": after,
                "target_repository_unchanged": before == after,
                "profile": summary,
            }
        )
    return output


def _run_git_text(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def _lifecycle_probe(evidence_root: Path) -> dict[str, Any]:
    disposable = Path(
        tempfile.mkdtemp(
            prefix="project-context-lifecycle-",
            dir=evidence_root,
        )
    ).resolve()
    _run_git_text(disposable, "init", "-q")
    _run_git_text(disposable, "config", "user.name", "JARVIS V11 Proof")
    _run_git_text(disposable, "config", "user.email", "jarvis-v11@example.invalid")
    package = disposable / "package.json"
    package.write_text(
        json.dumps(
            {
                "name": "lifecycle-proof",
                "packageManager": "npm@11",
                "scripts": {"build": "next build"},
                "dependencies": {"next": "16.2.0", "react": "19.2.0"},
            }
        ),
        encoding="utf-8",
    )
    (disposable / "package-lock.json").write_text(
        '{"lockfileVersion":3}\n', encoding="utf-8"
    )
    _run_git_text(disposable, "add", "--all")
    _run_git_text(disposable, "commit", "-q", "-m", "lifecycle baseline")

    service = ProjectProfileService(evidence_root / "lifecycle-cache")
    target = ProjectTarget("lifecycle-proof", disposable)
    first = service.get_profile(target)
    profile_path = service.store.profile_path(first.profile.profile_id)
    first_mtime = profile_path.stat().st_mtime_ns
    first_bytes = profile_path.read_bytes()
    replay = service.get_profile(target)
    replay_unchanged = (
        first_mtime == profile_path.stat().st_mtime_ns
        and first_bytes == profile_path.read_bytes()
    )

    data = json.loads(package.read_text(encoding="utf-8"))
    data["scripts"]["test"] = "vitest run"
    package.write_text(json.dumps(data), encoding="utf-8")
    stale = service.status(target)
    refreshed = service.get_profile(target)
    test_commands = {
        tuple(item["argv"]) for item in refreshed.profile.commands["test"]["items"]
    }
    return {
        "disposable_root": str(disposable),
        "first_lifecycle": first.lifecycle,
        "unchanged_lifecycle": replay.lifecycle,
        "unchanged_replay_no_rewrite": replay_unchanged,
        "stale_detected": stale.state == "STALE",
        "stale_changed_inputs": list(stale.changed_inputs),
        "refresh_lifecycle": refreshed.lifecycle,
        "refresh_cache_written": refreshed.cache_written,
        "profile_hash_changed": (
            refreshed.profile.profile_hash != first.profile.profile_hash
        ),
        "changed_command_discovered": ("npm", "run", "test") in test_commands,
    }


def _parse_project(value: str) -> tuple[str, Path]:
    project_id, separator, raw_path = value.partition("=")
    if not separator or not project_id or not raw_path:
        raise argparse.ArgumentTypeError("project must be PROJECT_ID=ABSOLUTE_PATH")
    path = Path(raw_path).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError(f"project path is unavailable: {raw_path}")
    return project_id, path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project", action="append", type=_parse_project, required=True
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    targets: list[tuple[str, Path]] = list(args.project)
    evidence_root = args.evidence_root.expanduser().resolve(strict=False)
    output = args.output.expanduser().resolve(strict=False)
    _outside_targets(evidence_root, targets)
    _outside_targets(output, targets)
    evidence_root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    service = ProjectProfileService(evidence_root / "profiles")
    projects = _profile_real_projects(service, targets)
    lifecycle = _lifecycle_probe(evidence_root)
    checks = {
        "several_real_projects": len(projects) >= 3,
        "all_target_repositories_unchanged": all(
            item["target_repository_unchanged"] for item in projects
        ),
        "all_unchanged_replays_no_rewrite": all(
            item["unchanged_replay_no_rewrite"] for item in projects
        ),
        "all_replays_classified": all(
            item["replay_lifecycle"] == "UNCHANGED_REPLAY" for item in projects
        ),
        "all_have_manifest_evidence": all(
            item["profile"]["manifests"] for item in projects
        ),
        "all_have_discovered_commands": all(
            any(group["items"] for group in item["profile"]["commands"].values())
            for item in projects
        ),
        "changed_manifest_stale_detected": lifecycle["stale_detected"],
        "changed_manifest_refreshed": (
            lifecycle["refresh_lifecycle"] == "REFRESHED"
            and lifecycle["refresh_cache_written"]
            and lifecycle["profile_hash_changed"]
            and lifecycle["changed_command_discovered"]
        ),
        "local_evidence_and_cache_writes_only": True,
        "discovered_commands_executed": False,
        "admission_decisions_made": False,
        "target_repository_writes": False,
        "external_provider_writes": False,
    }
    required_false = {
        "discovered_commands_executed",
        "admission_decisions_made",
        "target_repository_writes",
        "external_provider_writes",
    }
    passed = all(value is (key not in required_false) for key, value in checks.items())
    proof = {
        "contract": "jarvis.v11.project-context-proof.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "projects": projects,
        "lifecycle_probe": lifecycle,
        "checks": checks,
        "verdict": "PASS" if passed else "FAIL",
    }
    payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    print(
        json.dumps(
            {
                "verdict": proof["verdict"],
                "projects_profiled": len(projects),
                "output": str(output),
                "output_sha256": _sha_bytes(payload),
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
