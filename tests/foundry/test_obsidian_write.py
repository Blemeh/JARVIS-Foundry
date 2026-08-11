"""WP6 governed Obsidian write tests: note set, frontmatter contract,
transactional write protocol, boundary enforcement, and conflict detection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.obsidian import (
    FRONTMATTER,
    V1_NOTE_NAMES,
    ObsidianConflictError,
    ObsidianPolicyError,
    ObsidianService,
    ObsidianWriter,
)


def vault_fixture(tmp_path):
    """Temp vault (never the real one) with the V1 note set pre-seeded."""
    vault = tmp_path / "vault"
    (vault / "JARVIS" / "V1").mkdir(parents=True)
    (vault / "JARVIS" / "V1" / "Audit").mkdir()
    writer = ObsidianWriter(vault)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    service = ObsidianService(database, writer)
    for note in sorted(V1_NOTE_NAMES):
        path = writer.note_path(note)
        path.write_text(f"---\n{_fm()}\n---\n# {note}\n", encoding="utf-8")
    return vault, writer, database, service


def _fm() -> str:
    return "\n".join(f"{key}: {value}" for key, value in FRONTMATTER.items())


def note_content(title: str = "State") -> str:
    return f"---\n{_fm()}\n---\n# {title}\n\nbody\n"


def write_row(database, write_id: str) -> dict:
    return database.row("obsidian_writes", "write_id", write_id)


def test_v1_note_set_and_audit_dir_created(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    assert writer.write_root.exists()
    assert (writer.write_root / "Audit").is_dir()
    for note in V1_NOTE_NAMES:
        assert writer.note_path(note).exists()


def test_frontmatter_contract_required(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    with pytest.raises(ObsidianPolicyError, match="frontmatter contract|must start with a frontmatter"):
        service.write_note(note_name="00 - V1 State.md", content="# no frontmatter\n")
    with pytest.raises(ObsidianPolicyError, match="frontmatter contract"):
        service.write_note(
            note_name="00 - V1 State.md",
            content="---\norigin: someone_else\n---\n# wrong\n")
    # Nothing was written or recorded.
    assert "no frontmatter" not in writer.note_path("00 - V1 State.md").read_text(encoding="utf-8")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM obsidian_writes").fetchone()[0] == 0


def test_transactional_write_records_protocol_events(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("00 - V1 State.md")
    before = path.read_bytes()
    expected = hashlib.sha256(before).hexdigest()

    result = service.write_note(
        note_name="00 - V1 State.md", content=note_content("updated"),
        expected_hash=expected, mission_id="mission-1")

    assert result.status == "COMPLETED"
    assert result.after_hash == hashlib.sha256(note_content("updated").encode()).hexdigest()
    assert path.read_text(encoding="utf-8") == note_content("updated")
    row = write_row(database, result.write_id)
    assert row["status"] == "COMPLETED"
    assert row["expected_hash"] == expected
    assert row["after_hash"] == result.after_hash
    assert row["before_snapshot_path"] and Path(row["before_snapshot_path"]).exists()
    with database.connect() as connection:
        types = [r["event_type"] for r in connection.execute(
            "SELECT event_type FROM events WHERE event_type LIKE 'obsidian.%' ORDER BY sequence")]
    assert types == ["obsidian.write_started", "obsidian.write_completed"]


def test_expected_hash_mismatch_conflicts_and_preserves_both(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    path = writer.note_path("02 - Active Mission.md")
    before = path.read_bytes()
    stale_expected = hashlib.sha256(before).hexdigest()
    # A concurrent edit between our read and our write.
    path.write_text(note_content("concurrent owner edit"), encoding="utf-8")
    incoming = note_content("jarvis edit")

    with pytest.raises(ObsidianConflictError) as excinfo:
        service.write_note(
            note_name="02 - Active Mission.md", content=incoming,
            expected_hash=stale_expected)
    conflict = excinfo.value
    assert conflict.write_id is not None

    # The note keeps the concurrent edit; the incoming version is preserved.
    assert path.read_text(encoding="utf-8") == note_content("concurrent owner edit")
    assert Path(conflict.preserved_path).read_text(encoding="utf-8") == incoming
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM obsidian_writes WHERE note_name = '02 - Active Mission.md'").fetchall()
        events = connection.execute(
            "SELECT event_type FROM events WHERE event_type LIKE 'obsidian.%' ORDER BY sequence").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "CONFLICT"
    assert rows[0]["expected_hash"] == stale_expected
    preserved = Path(str(rows[0]["conflict_preserved_path"]))
    assert preserved.exists()
    assert preserved.read_text(encoding="utf-8") == incoming
    assert [e["event_type"] for e in events] == [
        "obsidian.write_started", "obsidian.conflict"]


def test_write_boundary_enforced(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    content = note_content()
    # Un-governed note name.
    with pytest.raises(ObsidianPolicyError, match="not in the governed"):
        service.write_note(note_name="09 - Freeform.md", content=content)
    # Traversal name.
    with pytest.raises(ObsidianPolicyError, match="plain file name"):
        service.write_note(note_name="../escape.md", content=content)
    # Audit/ cannot be written by callers (writer-managed).
    with pytest.raises(ObsidianPolicyError, match="plain file name|audit notes are written"):
        service.write_note(note_name="Audit/trail.md", content=content)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM obsidian_writes").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE 'obsidian.%'").fetchone()[0] == 0


def test_vault_read_allowed_write_restricted(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    # Whole-vault read works (a non-V1 note in the vault).
    other = vault / "Personal" / "note.md"
    other.parent.mkdir()
    other.write_text("owner note\n", encoding="utf-8")
    assert service.read_note("Personal/note.md") == "owner note\n"
    # Reads cannot escape the vault.
    with pytest.raises(ObsidianPolicyError, match="unsafe read path|escapes the vault"):
        service.read_note("../outside.md")
    with pytest.raises(ObsidianPolicyError, match="unsafe read path"):
        service.read_note("a/../../outside.md")


def test_rebuild_projections_reproduces_obsidian_writes(tmp_path):
    vault, writer, database, service = vault_fixture(tmp_path)
    service.write_note(note_name="03 - Decisions.md", content=note_content("decision"),
                       expected_hash=hashlib.sha256(
                           writer.note_path("03 - Decisions.md").read_bytes()).hexdigest())
    with database.connect() as connection:
        rows_before = connection.execute("SELECT * FROM obsidian_writes").fetchall()
    assert len(rows_before) == 1

    database.rebuild_projections()
    from jarvis_foundry.reconcile import reconcile
    report = reconcile(database)
    assert report.consistent is True
    with database.connect() as connection:
        rows_after = connection.execute("SELECT * FROM obsidian_writes").fetchall()
    assert len(rows_after) == 1
    assert dict(rows_after[0])["status"] == dict(rows_before[0])["status"]
    assert dict(rows_after[0])["after_hash"] == dict(rows_before[0])["after_hash"]
