"""WP11 slice F (V2-OPERATIONAL): governed consolidation.

Contract: consolidation happens ONLY in the JARVIS-governed vault area;
propose → verify (deterministic meaning check: every original statement
must survive canonically in the new content, frontmatter contract
intact) → accept (ObsidianService write with expected-hash conflict
protection: a concurrent edit CONFLICTS and both versions survive) →
rollback (byte-exact restore via the write's before snapshot; refuses
when the note changed again); every transition is an event; rebuild +
reconcile deterministic.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.obsidian.policy import ObsidianPolicyError, ObsidianWriter
from jarvis_foundry.obsidian.writer import ObsidianConflictError, ObsidianService

FRONTMATTER = ("---\norigin: jarvis\ncurrent_steward: jarvis\n"
               "system: jarvis-foundry\nversion: v1\n---\n\n")

NOTE_NAME = "03 - Decisions.md"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    note = FRONTMATTER + "# Notes\n\n- The gateway retries.\n- Backoff is exponential.\n"
    _write(root / "JARVIS" / "V1" / NOTE_NAME, note)
    return root


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


@pytest.fixture()
def service(db: FoundryDatabase, vault: Path) -> ObsidianService:
    writer = ObsidianWriter(vault, "JARVIS/V1")
    return ObsidianService(db, writer)


def _consolidated() -> str:
    return (FRONTMATTER + "# Notes (consolidated)\n\n"
            "- The gateway retries.\n- Backoff is exponential.\n"
            "- Source: JARVIS/V1/notes.md\n")


def test_propose_requires_governed_path(db: FoundryDatabase, vault: Path,
                                        service: ObsidianService):
    from jarvis_foundry.consolidation import propose_consolidation
    with pytest.raises(ValueError):
        propose_consolidation(db, service, "../outside.md", _consolidated(),
                              "merge")


def test_propose_and_verify_accepts_preserved_statements(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (propose_consolidation,
                                              verify_consolidation)
    op = propose_consolidation(db, service, "03 - Decisions.md", _consolidated(),
                               "merge")
    report = verify_consolidation(db, op["op_id"])
    assert report["verdict"] == "VERIFIED"
    row = db.row("consolidation_ops", "op_id", op["op_id"])
    assert row["status"] == "VERIFIED"


def test_verify_rejects_dropped_statements(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (propose_consolidation,
                                              verify_consolidation)
    bad = FRONTMATTER + "# Notes\n\n- The gateway retries.\n"  # dropped line
    op = propose_consolidation(db, service, "03 - Decisions.md", bad, "merge")
    report = verify_consolidation(db, op["op_id"])
    assert report["verdict"] == "REJECTED"
    # missing statements are reported in canonical form
    assert any("backoff is exponential" in item["statement"]
               for item in report["missing"])


def test_accept_writes_and_records_after_hash(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (accept_consolidation,
                                              propose_consolidation,
                                              verify_consolidation)
    op = propose_consolidation(db, service, "03 - Decisions.md", _consolidated(),
                               "merge")
    verify_consolidation(db, op["op_id"])
    result = accept_consolidation(db, service, op["op_id"])
    assert result["status"] == "ACCEPTED"
    row = db.row("consolidation_ops", "op_id", op["op_id"])
    assert row["status"] == "ACCEPTED"
    assert row["after_hash"] == _sha(_consolidated())
    assert (vault / "JARVIS" / "V1" / "03 - Decisions.md").read_text(
        encoding="utf-8") == _consolidated()


def test_concurrent_edit_conflicts_not_overwrites(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (accept_consolidation,
                                              propose_consolidation,
                                              verify_consolidation)
    op = propose_consolidation(db, service, "03 - Decisions.md", _consolidated(),
                               "merge")
    verify_consolidation(db, op["op_id"])
    # a human edits the note between proposal and accept
    human = FRONTMATTER + "# Notes\n\n- The gateway retries.\n- Human edit here.\n"
    _write(vault / "JARVIS" / "V1" / "03 - Decisions.md", human)
    with pytest.raises(ObsidianConflictError):
        accept_consolidation(db, service, op["op_id"])
    # the note on disk keeps the HUMAN edit; the op is rejected honestly
    assert (vault / "JARVIS" / "V1" / "03 - Decisions.md").read_text(
        encoding="utf-8") == human
    row = db.row("consolidation_ops", "op_id", op["op_id"])
    assert row["status"] == "REJECTED"


def test_rollback_restores_bytes(db: FoundryDatabase, vault: Path,
                                 service: ObsidianService):
    from jarvis_foundry.consolidation import (accept_consolidation,
                                              propose_consolidation,
                                              rollback_consolidation,
                                              verify_consolidation)
    original = (vault / "JARVIS" / "V1" / "03 - Decisions.md").read_text(
        encoding="utf-8")
    op = propose_consolidation(db, service, "03 - Decisions.md", _consolidated(),
                               "merge")
    verify_consolidation(db, op["op_id"])
    accept_consolidation(db, service, op["op_id"])
    rollback_consolidation(db, service, op["op_id"], "meaning degraded")
    assert (vault / "JARVIS" / "V1" / "03 - Decisions.md").read_text(
        encoding="utf-8") == original
    row = db.row("consolidation_ops", "op_id", op["op_id"])
    assert row["status"] == "ROLLED_BACK"


def test_rollback_refuses_when_note_changed_again(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (accept_consolidation,
                                              propose_consolidation,
                                              rollback_consolidation,
                                              verify_consolidation)
    op = propose_consolidation(db, service, "03 - Decisions.md",
                               _consolidated(), "merge")
    verify_consolidation(db, op["op_id"])
    accept_consolidation(db, service, op["op_id"])
    _write(vault / "JARVIS" / "V1" / "03 - Decisions.md",
           FRONTMATTER + "# Notes\n\n- Newer edit wins.\n")
    with pytest.raises(ObsidianConflictError):
        rollback_consolidation(db, service, op["op_id"], "trying")
    assert "Newer edit wins" in (vault / "JARVIS" / "V1" / "03 - Decisions.md").read_text(
        encoding="utf-8")


def test_rebuild_and_reconcile_deterministic(
        db: FoundryDatabase, vault: Path, service: ObsidianService):
    from jarvis_foundry.consolidation import (propose_consolidation,
                                              verify_consolidation)
    op = propose_consolidation(db, service, "03 - Decisions.md", _consolidated(),
                               "merge")
    verify_consolidation(db, op["op_id"])
    from jarvis_foundry.reconcile import reconcile
    before = db.all("consolidation_ops")
    db.rebuild_projections()
    assert db.all("consolidation_ops") == before
    assert reconcile(db).consistent
