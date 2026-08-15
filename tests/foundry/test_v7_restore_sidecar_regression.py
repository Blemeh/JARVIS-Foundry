"""V7-S5 R1 candidate — discriminating regression test for the WAL-safe
sidecar-aware restore procedure (V7-S2 finding: a naive file-copy restore
over a WAL-mode database is UNSAFE — stale -wal/-shm sidecars survive and
can win over the restored main file; the resulting state is undefined).

The test pins BOTH deterministic properties:
- the NAIVE procedure leaves the sidecars behind (the unsafe marker) and
  never guarantees exact restoration;
- the CORRECT procedure (close -> wal_checkpoint(TRUNCATE) -> delete
  -wal/-shm -> replace main file) restores the exact pre-migration state
  with no surviving sidecars.

Promoted into the suite by V7-S5 CYCLE-1 (R1).
"""
from __future__ import annotations

import gc
import hashlib
import shutil
import sqlite3
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase


def _fresh_db(tmp_path: Path) -> tuple[Path, int]:
    """A current-schema (v33) DB with a marker row and its own event count."""
    home = tmp_path / "db-home"
    home.mkdir()
    db_path = home / "foundry.db"
    db = FoundryDatabase(db_path)
    db.initialize()
    # Python's `with sqlite3.Connection` commits but does NOT close — the
    # leaked WAL connection holds the -wal lock on Windows. Checkpoint +
    # explicit close + gc so the sidecar is removable later.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    gc.collect()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO release_slots(slot, updated_at, "
                     "last_event_id) VALUES ('live', '2026-08-14T00:00:00Z', "
                     "'ev-0')")
        conn.execute("COMMIT")
    finally:
        conn.close()
    return db_path, len(db.events())


def _snapshot(db_path: Path, target: Path) -> str:
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    src.close()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _state(db_path: Path) -> dict[str, object]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    state = {
        "schema": con.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0],
        "live_slot": con.execute(
            "SELECT release_id FROM release_slots WHERE slot='live'"
        ).fetchone(),
        "quick_check": con.execute("PRAGMA quick_check").fetchone()[0],
    }
    con.close()
    return state


def _sidecars(db_path: Path) -> list[str]:
    """Filesystem-only sidecar check (no DB open — a read-only open
    itself recreates the -shm wal-index)."""
    return [s for s in ("-wal", "-shm") if Path(str(db_path) + s).exists()]


def _mutate(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE release_slots SET release_id='MUTATED' "
                     "WHERE slot='live'")
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_naive_copy_restore_over_wal_leaves_live_writer_sidecars(
        tmp_path: Path) -> None:
    """The trap: a naive restore replaces the main file while a WAL
    writer is alive and NEVER removes the -wal/-shm sidecars — the
    restored state is undefined (V7-S2: the stale WAL kept the migrated
    schema). A restore is only safe when no sidecars survive and the
    state is verified exact. This pins the hazard so the S6 procedure
    stays guarded."""
    db_path, _ = _fresh_db(tmp_path)
    snapshot = tmp_path / "snapshot.db"
    _snapshot(db_path, snapshot)
    conn = sqlite3.connect(db_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE release_slots SET release_id='MUTATED' "
                 "WHERE slot='live'")
    try:
        # naive restore with the writer STILL OPEN — sidecars survive
        shutil.copyfile(snapshot, db_path)
        assert _sidecars(db_path), (
            "naive restore with a live WAL writer must leave -wal/-shm "
            "sidecars in place — the documented trap")
    finally:
        conn.close()
    assert _state(snapshot)["live_slot"] == (None,), "snapshot sanity"


def test_sidecar_aware_restore_restores_exact_state(tmp_path: Path) -> None:
    """The correct procedure: close connections, checkpoint, delete the
    -wal/-shm sidecars, then replace the main file from the snapshot."""
    db_path, event_count = _fresh_db(tmp_path)
    snapshot = tmp_path / "snapshot.db"
    _snapshot(db_path, snapshot)
    assert _state(snapshot)["schema"] == 33

    _mutate(db_path)
    assert _state(db_path)["live_slot"] == ("MUTATED",)

    # correct procedure: checkpoint -> delete sidecars -> replace main file
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    gc.collect()
    for sidecar in ("-wal", "-shm"):
        p = Path(str(db_path) + sidecar)
        if p.exists():
            p.unlink()
    shutil.copyfile(snapshot, db_path)

    assert _sidecars(db_path) == [], (
        "a clean restore leaves no -wal/-shm sidecars behind")
    restored = _state(db_path)
    assert restored["schema"] == 33
    assert restored["live_slot"] == (None,), (
        "sidecar-aware restore must return the exact snapshot truth")
    assert restored["quick_check"] == "ok"
    assert len(FoundryDatabase(db_path).events()) == event_count
