"""Stage read-only real owner project metadata and vault metadata for R7 gates."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_UTC,
    EXCLUDED_VAULT_CATEGORIES,
    PROJECT_ROOTS,
    STAGED_ROOT,
    VAULT_ROOT,
    json_hash,
    safe_clear,
    sha256_file,
    write_json,
)

FIXED_MTIME_NS = 1_700_000_000_000_000_000
MAX_METADATA_BYTES = 65_536
VAULT_BODY_NAME_PATTERN = ("README.md",)
VAULT_BODY_SUFFIXES = ("Map.md", "North Star.md", "Welcome.md")


def _git(repo: Path, args: list[str]) -> str:
    from common import run

    return run(["git", "-C", str(repo), *args], timeout_seconds=60).get("output", "").strip()


def _stage_project(project_id: str, root: Path, manifest: dict[str, Any]) -> None:
    dest = STAGED_ROOT / "projects" / project_id
    dest.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        (dest / "git_status.txt").write_text("MISSING_ROOT\n", encoding="utf-8")
        (dest / "head.txt").write_text("MISSING_ROOT\n", encoding="utf-8")
        (dest / "last_commit.txt").write_text("MISSING_ROOT\n", encoding="utf-8")
        (dest / "top_level.json").write_text("[]\n", encoding="utf-8")
        manifest["projects"][project_id] = {
            "path": str(root),
            "exists": False,
            "head": None,
            "porcelain_count": 0,
            "git_status_sha256": sha256_file(dest / "git_status.txt"),
            "git_status_line_count": 1,
            "last_commit": None,
            "top_level_count": 0,
            "metadata_files": [],
        }
        return
    status = _git(root, ["status", "--porcelain"])
    head = _git(root, ["rev-parse", "HEAD"])
    last = _git(root, ["log", "-1", "--format=%H%x09%s"])
    (dest / "git_status.txt").write_text(status or "EMPTY\n", encoding="utf-8")
    (dest / "head.txt").write_text(head or "EMPTY\n", encoding="utf-8")
    (dest / "last_commit.txt").write_text(last or "EMPTY\n", encoding="utf-8")
    top_level: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        top_level.append(
            {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size_bytes": stat.st_size if entry.is_file() else None,
            }
        )
    (dest / "top_level.json").write_text(
        __import__("json").dumps(top_level, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metadata_files: list[dict[str, Any]] = []
    for candidate in (
        "README.md",
        "AGENTS.md",
        "PROJECT_STATE.md",
        "pyproject.toml",
        "package.json",
    ):
        source = root / candidate
        if source.is_file() and source.stat().st_size <= MAX_METADATA_BYTES:
            target = dest / candidate
            shutil.copy2(source, target)
            os.utime(target, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
            metadata_files.append(
                {
                    "rel_path": candidate,
                    "sha256": sha256_file(target),
                    "size_bytes": target.stat().st_size,
                }
            )
    manifest["projects"][project_id] = {
        "path": str(root),
        "exists": root.is_dir(),
        "head": head or None,
        "porcelain_count": len([line for line in status.splitlines() if line.strip()]),
        "git_status_sha256": sha256_file(dest / "git_status.txt"),
        "git_status_line_count": len([line for line in status.splitlines() if line.strip()]),
        "last_commit": last or None,
        "top_level_count": len(top_level),
        "metadata_files": metadata_files,
    }


def _allowed_vault_path(relative: Path) -> bool:
    parts = relative.parts
    return not any(part in EXCLUDED_VAULT_CATEGORIES or part.startswith(".") for part in parts)


def _vault_body_file(relative: Path) -> bool:
    name = relative.name
    return name in VAULT_BODY_NAME_PATTERN or name.endswith(VAULT_BODY_SUFFIXES)


def _stage_vault(manifest: dict[str, Any]) -> None:
    entries: list[dict[str, Any]] = []
    if not VAULT_ROOT.is_dir():
        manifest["vault"] = {
            "path": str(VAULT_ROOT),
            "available": False,
            "excluded_categories": list(EXCLUDED_VAULT_CATEGORIES),
        }
        return
    for path in sorted(VAULT_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(VAULT_ROOT)
        if not _allowed_vault_path(relative):
            continue
        stat = path.stat()
        entry: dict[str, Any] = {
            "rel_path": relative.as_posix(),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
            "body_staged": False,
        }
        if _vault_body_file(relative) and stat.st_size <= MAX_METADATA_BYTES:
            target = STAGED_ROOT / "vault" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            os.utime(target, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
            entry["body_staged"] = True
            entry["staged_sha256"] = sha256_file(target)
        entries.append(entry)
    manifest["vault"] = {
        "path": str(VAULT_ROOT),
        "available": True,
        "file_count": len(entries),
        "body_staged_count": sum(1 for item in entries if item["body_staged"]),
        "excluded_categories": list(EXCLUDED_VAULT_CATEGORIES),
        "files": entries,
    }


def main() -> int:
    safe_clear(STAGED_ROOT)
    STAGED_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "jarvis-r7-source-staging-v1",
        "staged_utc": EVIDENCE_UTC,
        "projects": {},
        "vault": {},
    }
    for project_id, root in PROJECT_ROOTS.items():
        _stage_project(project_id, root, manifest)
    _stage_vault(manifest)
    manifest["staging_hash"] = json_hash(manifest)
    output = STAGED_ROOT / "staging_manifest.json"
    write_json(output, manifest)
    print(
        f"staged projects={len(manifest['projects'])} "
        f"vault_files={manifest['vault'].get('file_count', 0)} "
        f"staging_hash={manifest['staging_hash']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
