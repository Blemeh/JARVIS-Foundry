"""WP11 slice E (V2-OPERATIONAL): real worker context manifests.

Contract: deliver_worker_context assembles a bounded context payload
(claims manifest + matching vault notes with text) for a real worker
run, records manifest_id/run_id/worker/mission_id/manifest_hash/sources/
truncation durably (row + worker.manifest_delivered event);
reconstruct_context rebuilds the EXACT payload from the recorded
sources (DB + vault, NO chat history) and reports whether the hash
matches — a replacement worker gets identical context while state is
unchanged, and a changed source is detected honestly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(root / "JARVIS" / "V1" / "retries.md",
           "The gateway retries.\nBackoff is exponential.\n")
    return root


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _seed(db: FoundryDatabase, vault: Path) -> None:
    from jarvis_foundry.vault_indexer import sync_vault
    from jarvis_foundry.live_ingestion import run_ingestion_cycle
    sync_vault(db, vault)
    run_ingestion_cycle(db, sources=["vault"], vault_root=vault)


def test_delivery_records_row_and_event(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import deliver_worker_context
    _seed(db, vault)
    record = deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    row = db.row("worker_manifests", "manifest_id", record["manifest_id"])
    assert row["run_id"] == "run-1"
    assert row["worker"] == "builder"
    assert row["manifest_hash"] == record["manifest_hash"]
    with db.connect() as connection:
        events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'worker.manifest_delivered'").fetchone()[0]
    assert events == 1


def test_delivery_is_deterministic(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import deliver_worker_context
    _seed(db, vault)
    first = deliver_worker_context(
        db, vault, run_id="run-a", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    second = deliver_worker_context(
        db, vault, run_id="run-b", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    assert first["manifest_hash"] == second["manifest_hash"]


def test_sources_recorded(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import deliver_worker_context
    _seed(db, vault)
    record = deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    sources = json.loads(record["sources_json"])
    assert any("JARVIS/V1/retries.md" in s for s in sources["claims"])
    assert "JARVIS/V1/retries.md" in sources["notes"]


def test_reconstruction_matches_without_chat_history(
        db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import (deliver_worker_context,
                                               reconstruct_context)
    _seed(db, vault)
    record = deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    rebuilt = reconstruct_context(db, vault, run_id="run-1")
    assert rebuilt["manifest_hash"] == record["manifest_hash"]
    assert rebuilt["matches_recorded"] is True


def test_reconstruction_detects_changed_source(
        db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import (deliver_worker_context,
                                               reconstruct_context)
    _seed(db, vault)
    deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    # the vault source changes → reconstruction must differ honestly
    _write(vault / "JARVIS" / "V1" / "retries.md",
           "The gateway retries at most three times.\nBackoff is capped.\n")
    rebuilt = reconstruct_context(db, vault, run_id="run-1")
    assert rebuilt["matches_recorded"] is False


def test_truncation_recorded(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import deliver_worker_context
    _seed(db, vault)
    for i in range(30):
        from jarvis_foundry.events import Event
        db.append(Event.create(
            "knowledge.claim_recorded",
            {"claim_id": f"bulk{i}", "mission_id": "mission-wfm-optimizer",
             "statement": f"The gateway retries item {i} with a very long "
                          f"description of the retry policy in detail.",
             "claim_type": "fact", "confidence": 0.5,
             "source": {"run_id": "r", "event_id": "e"}},
            source_type="test", source_id="test-suite",
            idempotency_key=f"knowledge.claim_recorded:bulk{i}"))
    record = deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway",
        max_chars=1500)
    truncation = json.loads(record["truncation_json"])
    assert truncation["claims_truncated"] is True
    assert record["manifest_hash"]


def test_rebuild_and_reconcile_deterministic(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.worker_context import deliver_worker_context
    _seed(db, vault)
    deliver_worker_context(
        db, vault, run_id="run-1", worker="builder",
        mission_id="mission-wfm-optimizer", objective="gateway retries")
    from jarvis_foundry.reconcile import reconcile
    before = db.all("worker_manifests")
    db.rebuild_projections()
    assert db.all("worker_manifests") == before
    assert reconcile(db).consistent
