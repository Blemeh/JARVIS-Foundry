"""V2 slice-5 (WP9): snapshots, backup, restore, semantic rollback.

Bounded by design:
- create_snapshot: records a snapshot row (ledger head + schema +
  counts) AND writes a consistent file-level backup via the SQLite
  backup API; the snapshot path is integrity-verifiable;
- open_snapshot_readonly: opens a snapshot copy READ-ONLY (restore
  verification; never mutated);
- semantic_rollback: APPEND-ONLY — claims are flagged ROLLED_BACK with
  the snapshot id + reason; history is preserved, never rewritten
  (the ledger remains the source of truth; rebuild reproduces the
  flags)."""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .database import FoundryDatabase
from .events import Event


class SnapshotReader:
    """Read-only view over a snapshot file (restore verification)."""

    def __init__(self, path: Path) -> None:
        self._uri = f"file:{path.as_posix()}?mode=ro"
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._uri, uri=True)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def create_snapshot(db: FoundryDatabase, mission_id: str, scope: str,
                    snap_dir: Path) -> dict[str, Any]:
    """Record + write a snapshot. Returns {snapshot_id, path, head_event_id}."""
    snap_dir = Path(snap_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
    snapshot_path = snap_dir / f"{snapshot_id}.db"

    with db.connect() as connection:
        head = connection.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM events").fetchone()[0]
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        claims = connection.execute(
            "SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
        # consistent file-level backup (SQLite backup API)
        dest = sqlite3.connect(snapshot_path)
        try:
            connection.backup(dest)
        finally:
            dest.close()
    content_sha = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

    # record AFTER the backup so the event carries the file checksum —
    # verification cross-checks the FILE against the authoritative
    # ledger-projected record (never self-attests from the file's own row;
    # verifier deleg_9ecde3af F2)
    db.append(Event.create(
        "knowledge.snapshot_created",
        {"snapshot_id": snapshot_id, "mission_id": mission_id,
         "scope": scope, "head_event_id": head,
         "schema_version": version, "claims_count": claims,
         "content_sha256": content_sha, "path": str(snapshot_path)},
        source_type="supervisor", source_id="wp9-snapshots",
        idempotency_key=f"knowledge.snapshot_created:{snapshot_id}"))
    return {"snapshot_id": snapshot_id, "path": snapshot_path,
            "head_event_id": head}


def verify_snapshot(db: FoundryDatabase, snapshot_id: str) -> bool:
    """Integrity check against the AUTHORITATIVE live-DB record:
    quick_check (structural) + content sha256 + claims count must all
    match the recorded snapshot row. Any payload tampering or structural
    damage fails verification."""
    try:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT path, claims_count, content_sha256 "
                "FROM knowledge_snapshots WHERE snapshot_id = ?",
                (snapshot_id,)).fetchone()
        if row is None:
            return False
        path, expected_claims, expected_sha = row
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro",
                               uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            claims = conn.execute(
                "SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
        finally:
            conn.close()
        if claims != expected_claims:
            return False
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected_sha
    except (sqlite3.Error, OSError, ValueError):
        return False


def open_snapshot_readonly(path: Path) -> SnapshotReader:
    return SnapshotReader(Path(path))


def semantic_rollback(db: FoundryDatabase, mission_id: str, snapshot_id: str,
                      claim_ids: list[str], reason: str) -> int:
    """Flag the given claims ROLLED_BACK (append-only; history preserved).
    Validates the snapshot exists; raises + rolls back otherwise.
    Deterministic idempotency key: one rollback per snapshot (verifier
    deleg_9ecde3af F7)."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT snapshot_id FROM knowledge_snapshots "
            "WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise ValueError(
                f"semantic_rollback requires an existing snapshot "
                f"({snapshot_id}) — rolled back")
    db.append(Event.create(
        "knowledge.claims_rolled_back",
        {"snapshot_id": snapshot_id, "mission_id": mission_id,
         "claim_ids": claim_ids, "reason": reason},
        source_type="supervisor", source_id="wp9-snapshots",
        idempotency_key=f"knowledge.claims_rolled_back:{snapshot_id}"))
    return len(claim_ids)
