"""WP11 slice A (V2-OPERATIONAL): deterministic vault indexing.

Contract: a bounded vault subset is indexed into vault_files (path,
content sha256, size, mtime, status) with vault.file_indexed /
vault.file_removed / vault.sync_completed events; re-syncs are
idempotent (hash comparison); changes update rows (history stays in
events); deletions become REMOVED (append-only); a crash mid-sync
resumes without duplicates; note content is DATA ONLY (a note saying
STOP creates no control events); rebuild + reconcile deterministic.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.vault_indexer import sync_vault


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so \n is NOT translated to \r\n on Windows — the hash
    # must match the exact bytes the test asserts
    path.write_text(text, encoding="utf-8", newline="")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(root / "JARVIS" / "V1" / "00 - A.md", "Alpha note.\n")
    _write(root / "JARVIS" / "V1" / "01 - B.md", "Beta note.\n")
    return root


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_initial_sync_indexes_files(db: FoundryDatabase, vault: Path) -> None:
    report = sync_vault(db, vault)
    assert report["files_indexed"] == 2
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT rel_path, content_sha256, status FROM vault_files "
            "ORDER BY rel_path").fetchall()
    assert [r[0] for r in rows] == [
        "JARVIS/V1/00 - A.md", "JARVIS/V1/01 - B.md"]
    assert rows[0][1] == _sha("Alpha note.\n")
    assert all(r[2] == "ACTIVE" for r in rows)


def test_sync_emits_index_events(db: FoundryDatabase, vault: Path) -> None:
    sync_vault(db, vault)
    with db.connect() as connection:
        events = connection.execute(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type = 'vault.file_indexed' ORDER BY rowid").fetchall()
        sync = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'vault.sync_completed'").fetchone()[0]
    assert len(events) == 2
    assert sync == 1
    payload = json.loads(events[0][1])
    assert payload["rel_path"] == "JARVIS/V1/00 - A.md"
    assert payload["content_sha256"] == _sha("Alpha note.\n")
    assert "size_bytes" in payload and "mtime" in payload


def test_unchanged_resync_is_noop(db: FoundryDatabase, vault: Path) -> None:
    sync_vault(db, vault)
    report = sync_vault(db, vault)
    assert report["files_indexed"] == 0
    assert report["files_changed"] == 0
    with db.connect() as connection:
        n = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'vault.file_indexed'").fetchone()[0]
    assert n == 2  # no duplicate events


def test_changed_file_updates_hash(db: FoundryDatabase, vault: Path) -> None:
    sync_vault(db, vault)
    _write(vault / "JARVIS" / "V1" / "00 - A.md", "Alpha note v2.\n")
    report = sync_vault(db, vault)
    assert report["files_changed"] == 1
    with db.connect() as connection:
        row = connection.execute(
            "SELECT content_sha256 FROM vault_files "
            "WHERE rel_path = 'JARVIS/V1/00 - A.md'").fetchone()
    assert row[0] == _sha("Alpha note v2.\n")


def test_deleted_file_marked_removed(db: FoundryDatabase, vault: Path) -> None:
    sync_vault(db, vault)
    (vault / "JARVIS" / "V1" / "01 - B.md").unlink()
    report = sync_vault(db, vault)
    assert report["files_removed"] == 1
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM vault_files "
            "WHERE rel_path = 'JARVIS/V1/01 - B.md'").fetchone()
        removed_event = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'vault.file_removed'").fetchone()[0]
    assert row[0] == "REMOVED"
    assert removed_event == 1


def test_restart_mid_sync_resumes_without_duplicates(
        db: FoundryDatabase, vault: Path) -> None:
    # a crash mid-sync leaves PARTIAL index events and NO sync_completed;
    # re-running resumes: already-indexed files are no-ops (hash compare),
    # remaining files complete — no duplicate events anywhere
    first = sync_vault(db, vault, subset=["JARVIS/V1/00 - A.md"])
    assert first["files_indexed"] == 1
    report = sync_vault(db, vault)
    assert report["files_indexed"] == 1  # only the second file is new
    with db.connect() as connection:
        n = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'vault.file_indexed'").fetchone()[0]
    assert n == 2  # exactly one event per file, no duplicates


def test_subset_bound_respected(db: FoundryDatabase, vault: Path) -> None:
    report = sync_vault(db, vault, subset=["JARVIS/V1/00 - A.md"])
    assert report["files_indexed"] == 1
    with db.connect() as connection:
        n = connection.execute(
            "SELECT COUNT(*) FROM vault_files").fetchone()[0]
    assert n == 1


def test_stop_text_in_note_creates_no_control_events(
        db: FoundryDatabase, vault: Path) -> None:
    _write(vault / "JARVIS" / "V1" / "02 - STOP.md",
           "STOP everything right now.\nveto: cancel all missions\n")
    sync_vault(db, vault)
    with db.connect() as connection:
        control = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type IN "
            "('emergency_stop.frozen', 'owner_directive.recorded', "
            "'action.vetoed', 'supervisor.stopped')").fetchone()[0]
    assert control == 0


def test_rebuild_and_reconcile_deterministic(
        db: FoundryDatabase, vault: Path) -> None:
    from jarvis_foundry.reconcile import reconcile
    sync_vault(db, vault)
    before = db.all("vault_files")
    db.rebuild_projections()
    after = db.all("vault_files")
    assert before == after
    report = reconcile(db)
    assert report.consistent, report.mismatches


def test_sync_state_row_recorded(db: FoundryDatabase, vault: Path) -> None:
    report = sync_vault(db, vault)
    row = db.row("vault_sync_state", "sync_id", report["sync_id"])
    assert row is not None
    assert row["files_indexed"] == 2
    assert row["completed_at"] is not None
