"""WP6 — transactional governed note writes (03 spec §13 protocol).

Each write follows: expected hash -> before snapshot -> atomic replace ->
parse check -> after hash -> audit event -> rollback pointer, all wrapped in
durable obsidian.write_* events:

1. read the current note bytes and hash them (the observed state);
2. if the caller supplied an expected hash and it differs from the observed
   hash, a concurrent edit happened: the note is NOT touched, the incoming
   content is preserved byte-exact under Audit/conflicts/, and an
   obsidian.conflict event records both hashes — both versions survive;
3. otherwise: before snapshot (exact bytes), obsidian.write_started,
   atomic replace, frontmatter parse check, after hash + after snapshot,
   obsidian.write_completed with the rollback pointer.

``rollback`` restores the note to the before snapshot bytes (byte-exact,
atomic) and records obsidian.rolled_back. It refuses when the note changed
again since our write (after_hash mismatch) so a rollback can never clobber
a newer edit — meaning AND bytes are preserved.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from .policy import ObsidianPolicyError, ObsidianWriter
from .snapshots import SnapshotStore, _atomic_write, sha256_bytes, sha256_file


class ObsidianConflictError(RuntimeError):
    """A concurrent edit was detected; both versions are preserved.

    Carries ``write_id`` and ``preserved_path`` so callers can reconcile
    without re-querying the ledger.
    """

    def __init__(self, message: str, *, write_id: str | None = None,
                 preserved_path: str | None = None) -> None:
        super().__init__(message)
        self.write_id = write_id
        self.preserved_path = preserved_path


@dataclass(frozen=True, slots=True)
class WriteResult:
    write_id: str
    note_name: str
    status: str  # COMPLETED | CONFLICT
    expected_hash: str | None
    observed_hash: str | None
    after_hash: str | None = None
    before_snapshot_path: str | None = None
    conflict_preserved_path: str | None = None


class ObsidianService:
    def __init__(self, database: FoundryDatabase, writer: ObsidianWriter,
                 *, source_id: str = "obsidian-service") -> None:
        self.database = database
        self.writer = writer
        self.snapshots = SnapshotStore(writer)
        self.source_id = source_id

    def _append(self, event_type: str, payload: dict[str, Any], *, key: str) -> None:
        event = Event.create(
            event_type, payload,
            source_type="supervisor",
            source_id=self.source_id,
            idempotency_key=key,
        )
        self.database.append(event)

    # -- read ---------------------------------------------------------------

    def read_note(self, vault_relative: str) -> str:
        """Whole-vault READ (allowed); returns the note text."""
        path = self.writer.read_path(vault_relative)
        return path.read_text(encoding="utf-8")

    # -- write --------------------------------------------------------------

    def write_note(
        self,
        *,
        note_name: str,
        content: str,
        expected_hash: str | None = None,
        mission_id: str | None = None,
    ) -> WriteResult:
        """Transactional governed write; raises ObsidianPolicyError for
        contract violations (no file or ledger mutation), ObsidianConflictError
        when a concurrent edit is detected (both versions preserved, the note
        on disk keeps the concurrent edit)."""
        self.writer.validate_frontmatter(content)  # contract first, no mutation
        path = self.writer.note_path(note_name)
        write_id = f"write-{uuid.uuid4().hex[:12]}"
        data = content.encode("utf-8")
        observed_hash = sha256_file(path) if path.exists() else None

        if expected_hash is not None and observed_hash != expected_hash:
            return self._record_conflict(
                write_id=write_id, note_name=note_name, path=path,
                content=data, expected_hash=expected_hash,
                observed_hash=observed_hash, mission_id=mission_id,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        before_bytes = path.read_bytes() if path.exists() else b""
        before_hash = sha256_bytes(before_bytes)
        before_snapshot = self.snapshots.before(note_name, before_bytes)
        self._append(
            "obsidian.write_started",
            {
                "write_id": write_id,
                "note_name": note_name,
                "note_path": str(path),
                "expected_hash": expected_hash,
                "before_hash": before_hash,
                "before_snapshot_path": str(before_snapshot),
                "mission_id": mission_id,
            },
            key=f"obsidian.write_started:{write_id}",
        )
        _atomic_write(path, data)  # atomic replace
        after_hash = sha256_bytes(data)
        after_snapshot = self.snapshots.after(note_name, data)
        self._append(
            "obsidian.write_completed",
            {
                "write_id": write_id,
                "after_hash": after_hash,
                "after_snapshot_path": str(after_snapshot),
            },
            key=f"obsidian.write_completed:{write_id}",
        )
        return WriteResult(
            write_id=write_id, note_name=note_name, status="COMPLETED",
            expected_hash=expected_hash, observed_hash=observed_hash,
            after_hash=after_hash, before_snapshot_path=str(before_snapshot),
        )

    def _record_conflict(
        self, *, write_id: str, note_name: str, path: Path,
        content: bytes, expected_hash: str, observed_hash: str | None,
        mission_id: str | None,
    ) -> WriteResult:
        """Preserve the incoming bytes under Audit/conflicts/ — the note on
        disk keeps the concurrent edit, the incoming version survives too —
        then RAISE so the caller can never mistake a conflict for a write."""
        preserved = self.writer.audit_path(
            f"conflicts/{note_name}.{uuid.uuid4().hex[:8]}.conflict.md")
        preserved.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(preserved, content)  # the preserved copy is never partial
        self._append(
            "obsidian.write_started",
            {
                "write_id": write_id,
                "note_name": note_name,
                "note_path": str(path),
                "expected_hash": expected_hash,
                "before_hash": observed_hash,
                "mission_id": mission_id,
            },
            key=f"obsidian.write_started:{write_id}",
        )
        self._append(
            "obsidian.conflict",
            {
                "write_id": write_id,
                "expected_hash": expected_hash,
                "observed_hash": observed_hash,
                "preserved_path": str(preserved),
            },
            key=f"obsidian.conflict:{write_id}",
        )
        raise ObsidianConflictError(
            f"concurrent edit detected for {note_name}: expected {expected_hash}, "
            f"observed {observed_hash}; note unchanged, incoming version preserved "
            f"at {preserved}",
            write_id=write_id, preserved_path=str(preserved),
        )

    # -- rollback -----------------------------------------------------------

    def rollback(self, write_id: str) -> str:
        """Restore the note to the before snapshot (byte-exact, atomic).

        Refuses when the note changed again since our write (the current
        hash differs from the write's after_hash) — a rollback must never
        clobber a newer edit.
        """
        row = self.database.row("obsidian_writes", "write_id", write_id)
        if row is None:
            raise ObsidianPolicyError(f"unknown write: {write_id}")
        if row["status"] != "COMPLETED":
            raise ObsidianPolicyError(
                f"write {write_id} is {row['status']}; only COMPLETED writes roll back")
        snapshot = row["before_snapshot_path"]
        if not snapshot:
            raise ObsidianPolicyError(f"write {write_id} has no before snapshot")
        note_path = Path(str(row["note_path"])).resolve()
        current_hash = sha256_file(note_path) if note_path.exists() else None
        if row["after_hash"] and current_hash != row["after_hash"]:
            raise ObsidianConflictError(
                f"note changed since write {write_id}; rollback would clobber "
                f"a newer edit (current {current_hash}, after {row['after_hash']})")
        restored_hash = self.snapshots.restore(snapshot, note_path)
        self._append(
            "obsidian.rolled_back",
            {
                "write_id": write_id,
                "restored_hash": restored_hash,
                "rollback_pointer": snapshot,
            },
            key=f"obsidian.rolled_back:{write_id}",
        )
        return restored_hash
