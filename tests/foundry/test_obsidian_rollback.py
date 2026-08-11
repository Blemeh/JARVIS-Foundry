"""WP6 rollback tests: byte-exact restore, refusal guards, and the
concurrent-edit preservation gate."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.obsidian import (
    ObsidianConflictError,
    ObsidianPolicyError,
    ObsidianService,
)

from test_obsidian_write import _fm, note_content, vault_fixture, write_row


def test_rollback_restores_exact_bytes(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("01 - Registered Project.md")
    original = path.read_bytes()
    result = service.write_note(
        note_name="01 - Registered Project.md", content=note_content("mutated"),
        expected_hash=hashlib.sha256(original).hexdigest())
    assert path.read_bytes() != original

    restored_hash = service.rollback(result.write_id)

    assert path.read_bytes() == original          # byte-exact restore
    assert restored_hash == hashlib.sha256(original).hexdigest()
    row = write_row(database, result.write_id)
    assert row["status"] == "ROLLED_BACK"
    assert row["rollback_pointer"] == row["before_snapshot_path"]
    with database.connect() as connection:
        events = connection.execute(
            "SELECT event_type FROM events WHERE event_type = 'obsidian.rolled_back'").fetchall()
    assert len(events) == 1


def test_rollback_refuses_when_note_changed_again(tmp_path):
    # A rollback must never clobber a newer edit: if the note changed since
    # our write, refuse (meaning AND bytes preserved).
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("07 - Daily State.md")
    result = service.write_note(
        note_name="07 - Daily State.md", content=note_content("jarvis v1"),
        expected_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    path.write_text(note_content("owner edit after jarvis"), encoding="utf-8")

    with pytest.raises(ObsidianConflictError, match="changed since"):
        service.rollback(result.write_id)

    assert path.read_text(encoding="utf-8") == note_content("owner edit after jarvis")
    row = write_row(database, result.write_id)
    assert row["status"] == "COMPLETED"  # not rolled back


def test_rollback_refuses_unknown_write(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    with pytest.raises(ObsidianPolicyError, match="unknown write"):
        service.rollback("write-does-not-exist")


def test_rollback_of_conflicted_write_refused(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("04 - Evidence Index.md")
    stale = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(note_content("owner edit"), encoding="utf-8")
    with pytest.raises(ObsidianConflictError):
        service.write_note(
            note_name="04 - Evidence Index.md", content=note_content("jarvis"),
            expected_hash=stale)

    with database.connect() as connection:
        write_id = connection.execute(
            "SELECT write_id FROM obsidian_writes WHERE note_name = '04 - Evidence Index.md'"
        ).fetchone()[0]
    with pytest.raises(ObsidianPolicyError, match="only COMPLETED writes roll back"):
        service.rollback(write_id)


def test_restore_refuses_target_outside_write_root(tmp_path):
    # Defense-in-depth (verifier observation): a restore can never write
    # outside the governed V1 write root, even with a forged target path.
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("07 - Daily State.md")
    result = service.write_note(
        note_name="07 - Daily State.md", content=note_content("v1"),
        expected_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    outside = tmp_path / "outside.txt"

    with pytest.raises(ValueError, match="escapes the V1 write root"):
        service.snapshots.restore(result.before_snapshot_path, outside)

    assert not outside.exists()


def test_rollback_then_rebuild_stays_consistent(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("06 - Lessons.md")
    original = path.read_bytes()
    result = service.write_note(
        note_name="06 - Lessons.md", content=note_content("lesson"),
        expected_hash=hashlib.sha256(original).hexdigest())
    service.rollback(result.write_id)

    database.rebuild_projections()
    from jarvis_foundry.reconcile import reconcile
    assert reconcile(database).consistent is True
    row = write_row(database, result.write_id)
    assert row["status"] == "ROLLED_BACK"
    assert row["rollback_pointer"] is not None
    assert path.read_bytes() == original
