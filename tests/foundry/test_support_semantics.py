"""V4-S1: source-kind-aware claim support + provenance hygiene (R5).

Contract (DIR-2026-08-12 item 5 + the V4 work package's FIRST MISSION):
- note_support resolves SUPPORT by SOURCE KIND: vault (registry ACTIVE,
  + file content-hash and line-hash when the root is available), handoff
  /research (file exists + line-hash), repo (commit exists in the
  bounded git log), system (event exists on the ledger). Claims whose
  source cannot be verified are UNSUPPORTED — no fabricated support.
- malformed duplicated provenance ("handoff:handoff:...") is normalized
  at the PROJECTION boundary (derived state; history untouched).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.vault_retrieval import batch_support, note_support


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def line_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()[:16]


def claim_event(db: FoundryDatabase, claim_id: str, statement: str,
                source_event: str, *, run_id: str = "r:1") -> None:
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": "mission-1",
         "statement": statement, "claim_type": "fact",
         "confidence": 0.5,
         "source": {"run_id": run_id, "event_id": source_event}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}"))


def support(db: FoundryDatabase, row: dict, **ctx) -> str:
    with db.connect() as connection:
        return note_support(connection, row, **ctx)


def claim_row(db: FoundryDatabase, claim_id: str) -> dict:
    with db.connect() as connection:
        return dict(connection.execute(
            "SELECT claim_id, source_run_id, source_event_id FROM "
            "knowledge_claims WHERE claim_id = ?", (claim_id,)).fetchone())


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(root / "JARVIS" / "V1" / "retries.md",
           "The gateway retries.\nBackoff is exponential.\n")
    return root


@pytest.fixture()
def handoffs(tmp_path: Path) -> Path:
    directory = tmp_path / "handoffs"
    _write(directory / "RECOVERY_NOTES.md",
           "The daemon heartbeats.\nLeases renew.\n")
    return directory


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"],
                   cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repository,
                   check=True)
    _write(repository / "README.md", "baseline\n")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline commit"],
                   cwd=repository, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                         check=True, capture_output=True, text=True).stdout.strip()
    return repository, sha


# ---------------------------------------------------------------------------
# VAULT kind
# ---------------------------------------------------------------------------

def test_vault_supported_when_active_and_line_matches(db, vault):
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    claim_event(db, "c-v1", "The gateway retries.",
                f"JARVIS/V1/retries.md:{line_hash('The gateway retries.')}")
    row = claim_row(db, "c-v1")
    assert support(db, row, vault_root=vault) == "SUPPORTED"


def test_vault_supported_registry_only_without_root(db, vault):
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    claim_event(db, "c-v2", "The gateway retries.",
                f"JARVIS/V1/retries.md:{line_hash('The gateway retries.')}")
    row = claim_row(db, "c-v2")
    # no root: registry ACTIVE alone supports (existing behavior preserved)
    assert support(db, row) == "SUPPORTED"


def test_vault_unsupported_when_line_changed(db, vault):
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    claim_event(db, "c-v3", "The gateway retries.",
                f"JARVIS/V1/retries.md:{line_hash('The gateway retries.')}")
    # the file changes: the referenced line no longer exists
    (vault / "JARVIS" / "V1" / "retries.md").write_text(
        "The gateway never retries.\n", encoding="utf-8", newline="")
    row = claim_row(db, "c-v3")
    assert support(db, row, vault_root=vault) == "UNSUPPORTED"


def test_vault_unsupported_when_file_missing_from_registry(db, vault):
    claim_event(db, "c-v4", "The gateway retries.",
                f"JARVIS/V1/ghost.md:{line_hash('The gateway retries.')}")
    row = claim_row(db, "c-v4")
    assert support(db, row, vault_root=vault) == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# HANDOFF kind
# ---------------------------------------------------------------------------

def test_handoff_supported_when_file_and_line_exist(db, handoffs):
    claim_event(db, "c-h1", "The daemon heartbeats.",
                f"handoff:RECOVERY_NOTES.md:{line_hash('The daemon heartbeats.')}")
    row = claim_row(db, "c-h1")
    assert support(db, row, handoff_dir=handoffs) == "SUPPORTED"


def test_handoff_unsupported_when_line_changed(db, handoffs):
    claim_event(db, "c-h2", "The daemon heartbeats.",
                f"handoff:RECOVERY_NOTES.md:{line_hash('The daemon heartbeats.')}")
    (handoffs / "RECOVERY_NOTES.md").write_text(
        "The daemon sleeps.\n", encoding="utf-8", newline="")
    row = claim_row(db, "c-h2")
    assert support(db, row, handoff_dir=handoffs) == "UNSUPPORTED"


def test_handoff_unsupported_when_file_missing(db, handoffs):
    claim_event(db, "c-h3", "The daemon heartbeats.",
                f"handoff:GONE.md:{line_hash('The daemon heartbeats.')}")
    row = claim_row(db, "c-h3")
    assert support(db, row, handoff_dir=handoffs) == "UNSUPPORTED"


def test_handoff_unsupported_without_dir(db):
    """No handoff dir = the referenced source cannot be verified."""
    claim_event(db, "c-h4", "The daemon heartbeats.",
                f"handoff:RECOVERY_NOTES.md:{line_hash('The daemon heartbeats.')}")
    row = claim_row(db, "c-h4")
    assert support(db, row) == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# REPO kind
# ---------------------------------------------------------------------------

def test_repo_supported_when_commit_in_log(db, repo):
    repository, sha = repo
    claim_event(db, "c-r1", "baseline commit",
                f"repo:{repository.name}:{sha}", run_id=f"repo:{repository.name}:{sha}")
    row = claim_row(db, "c-r1")
    assert support(db, row, repo_paths=(repository,)) == "SUPPORTED"


def test_repo_unsupported_when_commit_unknown(db, repo):
    repository, _ = repo
    claim_event(db, "c-r2", "ghost commit",
                f"repo:{repository.name}:{'0' * 40}",
                run_id=f"repo:{repository.name}:{'0' * 40}")
    row = claim_row(db, "c-r2")
    assert support(db, row, repo_paths=(repository,)) == "UNSUPPORTED"


def test_repo_unsupported_when_repo_not_provided(db, repo):
    repository, sha = repo
    claim_event(db, "c-r3", "baseline commit",
                f"repo:{repository.name}:{sha}",
                run_id=f"repo:{repository.name}:{sha}")
    row = claim_row(db, "c-r3")
    assert support(db, row) == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# SYSTEM kind (always checkable — the ledger is local)
# ---------------------------------------------------------------------------

def test_system_supported_when_event_exists(db):
    claim_event(db, "c-s1", "verification.passed k",
                "system:1", run_id="system:1")
    row = claim_row(db, "c-s1")
    assert support(db, row) == "SUPPORTED"


def test_system_unsupported_when_event_missing(db):
    claim_event(db, "c-s2", "verification.passed k",
                "system:999999", run_id="system:999999")
    row = claim_row(db, "c-s2")
    assert support(db, row) == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# Unknown kind + batch
# ---------------------------------------------------------------------------

def test_unknown_prefix_unsupported(db, handoffs, repo):
    claim_event(db, "c-u1", "mystery", "mystery:thing:hash")
    row = claim_row(db, "c-u1")
    assert support(db, row, handoff_dir=handoffs) == "UNSUPPORTED"


def test_batch_support_mixed_kinds(db, vault, handoffs, repo):
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    repository, sha = repo
    claim_event(db, "c-b1", "The gateway retries.",
                f"JARVIS/V1/retries.md:{line_hash('The gateway retries.')}")
    claim_event(db, "c-b2", "The daemon heartbeats.",
                f"handoff:RECOVERY_NOTES.md:{line_hash('The daemon heartbeats.')}")
    claim_event(db, "c-b3", "baseline commit",
                f"repo:{repository.name}:{sha}",
                run_id=f"repo:{repository.name}:{sha}")
    claim_event(db, "c-b4", "verification.passed k", "system:1", run_id="system:1")
    claim_event(db, "c-b5", "ghost", "handoff:GONE.md:abc")
    with db.connect() as connection:
        rows = [dict(r) for r in connection.execute(
            "SELECT claim_id, source_run_id, source_event_id FROM "
            "knowledge_claims ORDER BY claim_id").fetchall()]
        result = batch_support(connection, rows, vault_root=vault,
                               handoff_dir=handoffs, repo_paths=(repository,))
    assert result == {"c-b1": "SUPPORTED", "c-b2": "SUPPORTED",
                      "c-b3": "SUPPORTED", "c-b4": "SUPPORTED",
                      "c-b5": "UNSUPPORTED"}


# ---------------------------------------------------------------------------
# Provenance hygiene: run_id normalization at the projection boundary
# ---------------------------------------------------------------------------

def test_doubled_handoff_run_id_normalized_in_projection(db):
    """The malformed 'handoff:handoff:' run_id (766 live claims) is
    repaired at the PROJECTION boundary — the event payload keeps the
    historical value, the derived row is clean."""
    claim_event(db, "c-n1", "The daemon heartbeats.",
                f"handoff:RECOVERY_NOTES.md:{line_hash('The daemon heartbeats.')}",
                run_id="handoff:handoff:RECOVERY_NOTES.md:deadbeef")
    row = claim_row(db, "c-n1")
    assert row["source_run_id"] == "handoff:RECOVERY_NOTES.md:deadbeef"
    # the historical payload is untouched
    with db.connect() as connection:
        payload = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'knowledge.claim_recorded' AND payload_json LIKE '%c-n1%'"
        ).fetchone()[0]
    assert "handoff:handoff:RECOVERY_NOTES.md:deadbeef" in payload


def test_research_doubled_run_id_normalized(db):
    claim_event(db, "c-n2", "x", "research:FILE.md:abc",
                run_id="research:research:FILE.md:abc")
    assert claim_row(db, "c-n2")["source_run_id"] == "research:FILE.md:abc"


def test_single_prefix_run_ids_untouched(db):
    claim_event(db, "c-n3", "x", "vault:rel:abc", run_id="vault:rel:abc")
    claim_event(db, "c-n4", "x", "system:1", run_id="system:1")
    assert claim_row(db, "c-n3")["source_run_id"] == "vault:rel:abc"
    assert claim_row(db, "c-n4")["source_run_id"] == "system:1"
