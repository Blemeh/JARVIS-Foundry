from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import SourceSpec, read_vault_source
from jarvis_foundry.vault_indexer import configured_vault_root, sync_vault


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def test_a2_default_vault_source_uses_latest_index_root(
        db: FoundryDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_foundry.initiative_a2 import _with_default_vault_source

    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    root = tmp_path / "owner-vault"
    _write(root / "goals.md", "Standing goal context.")
    sync_vault(db, root)

    sources = _with_default_vault_source(db, (), source_limit=8)

    assert len(sources) == 1
    assert sources[0].source_id == "owner-vault"
    assert sources[0].source_kind == "vault"
    assert Path(sources[0].source_identity) == root.resolve()
    assert configured_vault_root(db) == root.resolve()


def test_vault_observation_refreshes_index_without_making_a_candidate(
        db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "owner-vault"
    _write(root / "goals.md", "Standing goal context.")
    spec = SourceSpec("owner-vault", "vault", str(root), poll_interval_s=300)

    result = read_vault_source(spec, database=db)

    assert result.status == "READY"
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.authority == "DATA_ONLY"
    assert observation.candidate_on == "never"
    assert observation.payload["active_file_count"] == 1
    assert observation.payload["index_hash"]
    assert db.count("vault_sync_state") == 1


def test_vault_observation_blocks_a_root_switch_before_deindexing(
        db: FoundryDatabase, tmp_path: Path) -> None:
    indexed_root = tmp_path / "owner-vault"
    other_root = tmp_path / "other-vault"
    _write(indexed_root / "goals.md", "Standing goal context.")
    _write(other_root / "different.md", "Other content.")
    sync_vault(db, indexed_root)

    result = read_vault_source(
        SourceSpec("owner-vault", "vault", str(other_root)), database=db,
    )

    assert result.status == "BLOCKED"
    assert "differs from the indexed root" in result.reason
    row = db.row("vault_files", "rel_path", "goals.md")
    assert row is not None and row["status"] == "ACTIVE"
    assert db.row("vault_files", "rel_path", "different.md") is None


def test_stop_during_vault_indexing_leaves_no_completed_sync(
        db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "owner-vault"
    _write(root / "a.md", "A")
    _write(root / "b.md", "B")
    calls = 0

    def stop_after_one_file() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    result = read_vault_source(
        SourceSpec("owner-vault", "vault", str(root)),
        database=db,
        stop_check=stop_after_one_file,
    )

    assert result.status == "BLOCKED"
    assert result.reason == "stop requested during vault indexing"
    assert db.count("vault_sync_state") == 0
