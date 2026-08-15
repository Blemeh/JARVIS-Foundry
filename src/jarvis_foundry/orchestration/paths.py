"""Path confinement and hashing helpers for the R8 proving area."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path

TEMP_BASE = Path(
    os.environ.get(
        "JARVIS_R8_TEMP",
        "C:/Users/b/AppData/Local/Temp/jarvis-r8-builder",
    )
)


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve a candidate path and require it to stay inside root."""
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RuntimeError(f"path escapes proving root {resolved_root}: {resolved}")
    return resolved


def ensure_relative_safe(relative: str) -> str:
    """Validate a worker-reported relative path for confinement."""
    if not relative:
        raise ValueError("empty relative path")
    if relative.startswith(("/", "\\")):
        raise ValueError(f"absolute path refused: {relative!r}")
    if "\\" in relative:
        raise ValueError(f"backslash path refused: {relative!r}")
    if ":" in relative:
        raise ValueError(f"drive/URI path refused: {relative!r}")
    if "\x00" in relative:
        raise ValueError(f"NUL byte refused: {relative!r}")
    parts = Path(relative).parts
    if any(part in {"..", ""} for part in parts) or any(part.startswith(":") for part in parts):
        raise ValueError(f"unsafe relative path refused: {relative!r}")
    return "/".join(parts)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"sha256": sha256_file(path), "size": stat.st_size}


def tree_manifest(
    root: Path, *, ignore_dirs: set[str] | None = None
) -> dict[str, dict[str, object]]:
    """Deterministic manifest of every file under root (excluding .git)."""
    ignored = ignore_dirs or {".git", "__pycache__"}
    manifest: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        manifest[relative.as_posix()] = file_entry(path)
    return manifest


def tree_hash(root: Path) -> str:
    """Content hash over the complete file manifest of a tree."""
    from jarvis_foundry.kernel.canonical import canonical_hash

    return canonical_hash(tree_manifest(root))


def safe_clear(path: Path) -> None:
    """Clear an isolated proving-area path only; never a protected path."""
    clear_within(path, TEMP_BASE)


def clear_within(path: Path, root: Path) -> None:
    """Clear a path only when it resolves inside the given root."""

    def _on_rm_error(function: object, target: str, exc_info: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        if callable(function):
            function(target)

    resolved = path.resolve()
    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RuntimeError(f"refusing to clear outside {resolved_root}: {resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved, onexc=_on_rm_error)
    elif resolved.is_file():
        resolved.unlink()


def copy_tree(source: Path, destination: Path, *, exclude: set[str] | None = None) -> None:
    """Copy a tree into an isolated destination, excluding named relative parts."""
    excluded = {".git", "__pycache__"} if exclude is None else exclude
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in excluded for part in relative.parts):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
