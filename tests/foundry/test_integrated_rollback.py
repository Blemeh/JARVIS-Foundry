"""WP11 slice G (V2-OPERATIONAL): integrated rollback.

Contract: the full chain — snapshot → mutation (governed consolidation)
→ degradation detection (statement preservation) → freeze (further
accepts on the note refused) → byte restoration (before-snapshot
bytes) → projection rebuild → semantic reconciliation (claims
re-flagged append-only, reconcile consistent, snapshot verifiable).
Restart-safe: a crash mid-flow resumes to exactly one restore (no
duplicate mutation); the restore refuses when the note changed again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.obsidian.policy import ObsidianWriter
from jarvis_foundry.obsidian.writer import ObsidianConflictError, ObsidianService

FRONTMATTER = ("---\norigin: jarvis\ncurrent_steward: jarvis\n"
               "system: jarvis-foundry\nversion: v1\n---\n\n")

NOTE_NAME = "04 - Evidence Index.md"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    note = FRONTMATTER + "# Evidence\n\n- The gateway retries.\n- Backoff is exponential.\n"
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


def _seed_claims(db: FoundryDatabase, vault: Path) -> None:
    from jarvis_foundry.vault_indexer import sync_vault
    from jarvis_foundry.live_ingestion import run_ingestion_cycle
    sync_vault(db, vault)
    run_ingestion_cycle(db, sources=["vault"], vault_root=vault)


def test_full_chain_restores_bytes_and_reconciles(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)
    original = (vault / "JARVIS" / "V1" / NOTE_NAME).read_text(
        encoding="utf-8")

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n- Extra.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    def degrade():
        # a later edit drops a statement → semantic degradation
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    report = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert report["degraded"] is True
    assert report["restored"] is True
    # bytes restored to the pre-mutation state
    assert (vault / "JARVIS" / "V1" / NOTE_NAME).read_text(
        encoding="utf-8") == original
    assert report["reconciled"] is True


def test_no_degradation_no_rollback(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)
    original = (vault / "JARVIS" / "V1" / NOTE_NAME).read_text(
        encoding="utf-8")

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n- Extra.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    report = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate)
    assert report["degraded"] is False
    assert report["restored"] is False
    # the consolidation stays accepted; the note keeps the new content
    assert "consolidated" in (vault / "JARVIS" / "V1" / NOTE_NAME).read_text(
        encoding="utf-8")


def test_freeze_refuses_further_accepts_until_restored(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.consolidation import accept_consolidation
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)
    state = {}

    def mutate():
        from jarvis_foundry.consolidation import (propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        state["op"] = op
        return op

    def degrade():
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    report = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert report["restored"] is True
    # while a freeze is active, a NEW accept on the same note is refused
    with pytest.raises(ValueError):
        accept_consolidation(db, service, state["op"]["op_id"])


def test_restart_mid_flow_no_duplicate_restore(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    def degrade():
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    first = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert first["restored"] is True
    # a crash "after completion" → re-running is a no-op: no duplicate
    # mutation, no second restore
    second = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert second["restored"] is False
    assert second["noop"] is True


def test_claims_semantically_rolled_back(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)
    with db.connect() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE status = 'ACCEPTED'"
        ).fetchone()[0]
    assert before >= 2

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    def degrade():
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    report = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert report["claims_rolled_back"] >= 2
    with db.connect() as connection:
        rolled = connection.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE status = 'ROLLED_BACK'"
        ).fetchone()[0]
    assert rolled >= 2


def test_restore_refuses_when_note_changed_again(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    _seed_claims(db, vault)
    failed = {}

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    def degrade():
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    def after_freeze():
        # a human edit lands AFTER the freeze, before the restore
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- Human edit during freeze.\n")

    try:
        run_integrated_rollback(
            db, service, mission_id="mission-wfm-optimizer",
            note_name=NOTE_NAME, snapshot_dir=tmp_path / "snaps",
            mutate_fn=mutate, degrade_fn=degrade,
            after_freeze_fn=after_freeze)
    except ObsidianConflictError:
        failed["conflict"] = True
    assert failed.get("conflict") is True
    # the human edit survived (a rollback never clobbers a newer edit)
    assert "Human edit during freeze" in (
        vault / "JARVIS" / "V1" / NOTE_NAME).read_text(encoding="utf-8")


def test_snapshot_verified_after_chain(
        db: FoundryDatabase, vault: Path, service: ObsidianService,
        tmp_path: Path):
    from jarvis_foundry.integrated_rollback import run_integrated_rollback
    from jarvis_foundry.knowledge_snapshots import verify_snapshot
    _seed_claims(db, vault)

    def mutate():
        from jarvis_foundry.consolidation import (accept_consolidation,
                                                  propose_consolidation,
                                                  verify_consolidation)
        content = FRONTMATTER + "# Evidence (consolidated)\n\n" \
            "- The gateway retries.\n- Backoff is exponential.\n"
        op = propose_consolidation(db, service, NOTE_NAME, content, "merge")
        verify_consolidation(db, op["op_id"])
        accept_consolidation(db, service, op["op_id"])
        return op

    def degrade():
        _write(vault / "JARVIS" / "V1" / NOTE_NAME,
               FRONTMATTER + "# Evidence\n\n- The gateway retries.\n")
        return True

    report = run_integrated_rollback(
        db, service, mission_id="mission-wfm-optimizer", note_name=NOTE_NAME,
        snapshot_dir=tmp_path / "snaps", mutate_fn=mutate,
        degrade_fn=degrade)
    assert verify_snapshot(db, report["snapshot_id"]) is True
    assert report["reconciled"] is True
