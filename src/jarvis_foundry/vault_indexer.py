"""WP11 slice A (V2-OPERATIONAL): deterministic vault indexing.

Deterministic + idempotent by construction:
- file identity = rel_path within the bounded subset; state = sha256 +
  size + mtime; rows are PROJECTED from events (vault.file_indexed /
  vault.file_removed / vault.sync_completed) so rebuild + reconcile
  reproduce them exactly;
- every mutation is its own event append (no giant transaction) so a
  crash mid-sync leaves a partial-but-consistent state; re-running
  compares hashes and completes the rest — restart-safe;
- note content is DATA ONLY — never parsed for control; a note saying
  STOP produces file rows, nothing else.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Iterable

from .database import FoundryDatabase
from .events import Event

_INDEXABLE = {".md", ".txt", ".markdown"}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path, subset: Iterable[str] | None) -> list[Path]:
    if subset is not None:
        return [root / rel for rel in subset if (root / rel).is_file()]
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _INDEXABLE)


def _known_active(db: FoundryDatabase) -> dict[str, str]:
    """rel_path -> content_sha256 for ACTIVE rows."""
    with db.connect() as connection:
        return {row[0]: row[1] for row in connection.execute(
            "SELECT rel_path, content_sha256 FROM vault_files "
            "WHERE status = 'ACTIVE'").fetchall()}


def sync_vault(db: FoundryDatabase, vault_root: str | Path,
               subset: Iterable[str] | None = None,
               *, source_id: str = "vault-indexer",
               started_at: str | None = None) -> dict[str, Any]:
    """Index (or refresh) the bounded vault subset. Returns the report.

    Restart-safe: each file mutation is its own event append; a crash
    leaves partial events and NO sync_completed; re-running re-checks
    hashes (already-indexed files no-op) and completes the rest.
    """
    root = Path(vault_root).resolve()
    sync_id = f"sync-{uuid.uuid4().hex[:12]}"
    started = started_at or "t0"

    files = _walk(root, subset)
    known = _known_active(db)
    indexed = changed = removed = 0

    for path in files:
        rel = path.relative_to(root).as_posix()
        digest = _file_sha256(path)
        size = path.stat().st_size
        mtime = str(path.stat().st_mtime_ns)
        current = known.get(rel)
        if current is None:
            db.append(Event.create(
                "vault.file_indexed",
                {"rel_path": rel, "content_sha256": digest,
                 "size_bytes": size, "mtime": mtime},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"vault.file_indexed:{rel}:{digest}:{mtime}"))
            indexed += 1
        elif current != digest:
            db.append(Event.create(
                "vault.file_indexed",
                {"rel_path": rel, "content_sha256": digest,
                 "size_bytes": size, "mtime": mtime},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"vault.file_indexed:{rel}:{digest}:{mtime}"))
            changed += 1

    for rel in known:
        if not (root / rel).is_file():
            db.append(Event.create(
                "vault.file_removed",
                {"rel_path": rel},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"vault.file_removed:{rel}"))
            removed += 1

    db.append(Event.create(
        "vault.sync_completed",
        {"sync_id": sync_id, "root": str(root), "started_at": started,
         "files_indexed": indexed, "files_changed": changed,
         "files_removed": removed,
         "cursor": str(files[-1].relative_to(root).as_posix()) if files else ""},
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"vault.sync_completed:{sync_id}"))
    return {"sync_id": sync_id, "files_indexed": indexed,
            "files_changed": changed, "files_removed": removed}
