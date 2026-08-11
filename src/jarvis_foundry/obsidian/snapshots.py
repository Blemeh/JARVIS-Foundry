"""WP6 — byte snapshots for governed Obsidian writes.

Every transactional write keeps a before snapshot (exact bytes) and the
write's after bytes under the JARVIS-owned ``.jarvis/snapshots/`` area of the
V1 write root. ``restore`` returns the note to the snapshot bytes with the
same atomic-replace discipline, so rollback is byte-exact and crash-safe.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .policy import ObsidianWriter


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (temp file + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


class SnapshotStore:
    """Before/after byte snapshots under ``<write_root>/.jarvis/snapshots``."""

    def __init__(self, writer: ObsidianWriter) -> None:
        self.writer = writer
        self.root = writer.write_root / ".jarvis" / "snapshots"

    def _note_dir(self, note_name: str) -> Path:
        safe = note_name.replace(" ", "_").replace(".md", "")
        path = (self.root / safe).resolve()
        if self.writer.write_root not in path.parents and path != self.root:
            raise ValueError("snapshot path escapes the JARVIS state area")
        return path

    def before(self, note_name: str, content: bytes) -> Path:
        """Snapshot the pre-write bytes; returns the snapshot path."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self._note_dir(note_name) / f"{stamp}.before"
        _atomic_write(path, content)
        return path

    def after(self, note_name: str, content: bytes) -> Path:
        """Snapshot the post-write bytes; returns the snapshot path."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self._note_dir(note_name) / f"{stamp}.after"
        _atomic_write(path, content)
        return path

    def restore(self, snapshot_path: str | Path, target: Path) -> str:
        """Restore ``target`` to the snapshot bytes (atomic); returns the
        restored content's sha256.

        Defense-in-depth: the target must resolve inside the V1 write root —
        a restore can never write outside the governed area even if a caller
        passes a forged path.
        """
        source = Path(snapshot_path)
        if not source.exists():
            raise FileNotFoundError(f"snapshot missing: {source}")
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(self.writer.write_root)
        except ValueError:
            raise ValueError(
                f"restore target escapes the V1 write root: {resolved_target}")
        data = source.read_bytes()
        _atomic_write(resolved_target, data)
        return sha256_bytes(data)
