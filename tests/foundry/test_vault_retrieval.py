"""WP11 slice B (V2-OPERATIONAL): whole-vault retrieval.

Contract: retrieve_vault returns a bounded envelope of SQLite knowledge
claims + real vault notes (read via the index allowlist) for a query,
with filters (mission, entity, claim_type, min_confidence, source,
time) and provenance/stale/support/contradictions surfaced; note text
is matched by deterministic token scoring (ALL tokens must match);
cross-mission leakage is measured; evaluate_retrieval computes
precision/recall/FP/FN/leakage over labeled probes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _claim(db: FoundryDatabase, claim_id: str, mission_id: str,
           statement: str, *, claim_type: str = "fact",
           confidence: float = 0.5, source_event: str | None = None,
           fresh_until: str | None = None) -> None:
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": claim_type,
         "confidence": confidence, "fresh_until": fresh_until,
         "source": {"run_id": f"doc-{hashlib.sha256(statement.encode()).hexdigest()[:16]}",
                    "event_id": source_event}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}"))


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(root / "JARVIS" / "V1" / "retries.md",
           "The gateway retries at most three times.\nBackoff is exponential.\n")
    _write(root / "JARVIS" / "V1" / "stale.md",
           "The old scheduler runs at midnight.\n")
    return root


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _seed(db: FoundryDatabase, vault: Path) -> None:
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    # mission-1 claims sourced from vault note retries.md
    _claim(db, "c1", "mission-1", "The gateway retries.",
           source_event="JARVIS/V1/retries.md")
    _claim(db, "c2", "mission-1", "Backoff is exponential.",
           confidence=0.8, source_event="JARVIS/V1/retries.md")
    # mission-2 claim (leakage target)
    _claim(db, "c3", "mission-2", "The old scheduler runs at midnight.",
           source_event="JARVIS/V1/stale.md")


def test_envelope_returns_claims_and_notes(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    result = retrieve_vault_conn(db, vault, "gateway retries")
    assert result["claim_count"] == 1
    assert result["claims"][0]["claim_id"] == "c1"
    assert result["note_count"] == 1
    assert result["notes"][0]["rel_path"] == "JARVIS/V1/retries.md"
    assert result["notes"][0]["content_sha256"]  # provenance hash present


def test_and_token_matching_excludes_partial(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    # "gateway midnight" matches NO single note/claim (tokens split)
    result = retrieve_vault_conn(db, vault, "gateway midnight")
    assert result["claim_count"] == 0
    assert result["note_count"] == 0


def test_mission_filter_excludes_other_mission_claims(
        db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    result = retrieve_vault_conn(db, vault, "scheduler",
                                 mission_id="mission-1")
    assert result["claim_count"] == 0
    # mission-2's claim must not leak
    result2 = retrieve_vault_conn(db, vault, "scheduler",
                                  mission_id="mission-2")
    assert result2["claim_count"] == 1
    assert result2["claims"][0]["mission_id"] == "mission-2"


def test_entity_filter(db: FoundryDatabase, vault: Path):
    from jarvis_foundry.events import Event as E
    db.append(E.create(
        "knowledge.entity_registered",
        {"entity_id": "ent-gateway", "mission_id": "mission-1",
         "canonical_name": "Gateway", "aliases": ["gateway"]},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.entity_registered:ent-gateway"))
    _claim(db, "c1", "mission-1", "The gateway retries.")
    db.append(E.create(
        "knowledge.claim_entity_linked",
        {"claim_id": "c1", "entity_id": "ent-gateway"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_entity_linked:c1"))
    result = retrieve_vault_conn(db, vault, "retries", entity="gateway")
    assert result["claim_count"] == 1
    assert result["claims"][0]["claim_id"] == "c1"


def test_confidence_filter(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    result = retrieve_vault_conn(db, vault, "backoff", min_confidence=0.7)
    assert result["claim_count"] == 1
    assert result["claims"][0]["claim_id"] == "c2"


def test_claim_type_filter(db: FoundryDatabase, vault: Path):
    _claim(db, "d1", "mission-1", "Retries must be bounded.",
           claim_type="constraint")
    result = retrieve_vault_conn(db, vault, "retries", claim_type="constraint")
    assert result["claim_count"] == 1
    assert result["claims"][0]["claim_type"] == "constraint"


def test_support_supported_and_unsupported(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    _claim(db, "c4x", "mission-1", "The gateway retries at most three times.",
           source_event="JARVIS/V1/gone.md")
    result = retrieve_vault_conn(db, vault, "gateway")
    by_id = {c["claim_id"]: c for c in result["claims"]}
    assert by_id["c1"]["support"] == "SUPPORTED"
    assert by_id["c4x"]["support"] == "UNSUPPORTED"


def test_stale_and_contradictions_surfaced(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    # same canonical core as c1 ("the gateway retries"), one negated → the
    # deterministic detector must have recorded a contradiction pair
    # ("never" is a negation token; "retries" is left uninflected so both
    # cores stay identical)
    _claim(db, "c5z", "mission-1", "The gateway never retries.",
           source_event="JARVIS/V1/retries.md")
    result = retrieve_vault_conn(db, vault, "gateway")
    claims = {c["claim_id"]: c for c in result["claims"]}
    assert "c5z" in claims
    assert any(c["contradictions"] for c in claims.values())


def test_limit_cap(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    for i in range(10):
        _claim(db, f"bulk{i}", "mission-1", f"The gateway retries item {i}.")
    result = retrieve_vault_conn(db, vault, "gateway", limit=5)
    assert result["claim_count"] <= 5
    assert result["truncated"] is True


def test_evaluate_retrieval_metrics(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    probes = [
        {"query": "gateway retries", "mission_id": "mission-1",
         "relevant_claims": ["c1"], "relevant_notes": ["JARVIS/V1/retries.md"]},
        {"query": "scheduler midnight", "mission_id": "mission-2",
         "relevant_claims": ["c3"], "relevant_notes": ["JARVIS/V1/stale.md"]},
    ]
    metrics = evaluate_retrieval_conn(db, vault, probes)
    assert metrics["true_positives"] == 4
    assert metrics["false_positives"] == 0
    assert metrics["false_negatives"] == 0
    assert metrics["cross_mission_leakage"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_path_traversal_rejected(db: FoundryDatabase, vault: Path):
    _seed(db, vault)
    # vault_files rows only come from the indexer; a rogue row pointing
    # outside must not be readable
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO vault_files(rel_path, content_sha256, size_bytes, "
            "mtime, first_seen, last_synced, status, last_event_id, updated_at) "
            "VALUES ('../evil.md', 'abc', 1, 't', 't', 't', 'ACTIVE', 'e', 't')")
    result = retrieve_vault_conn(db, vault, "evil")
    # the rogue path resolves outside the root → excluded (no crash)
    assert result["note_count"] == 0


def retrieve_vault_conn(db: FoundryDatabase, vault: Path, query: str, **kw):
    from jarvis_foundry.vault_retrieval import retrieve_vault
    with db.connect() as connection:
        return retrieve_vault(connection, vault, query, **kw)


def evaluate_retrieval_conn(db: FoundryDatabase, vault: Path, probes):
    from jarvis_foundry.vault_retrieval import evaluate_retrieval
    with db.connect() as connection:
        return evaluate_retrieval(connection, vault, probes)
