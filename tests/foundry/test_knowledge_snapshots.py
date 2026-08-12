# Slice-5 TDD test draft (applied to the repo only after slice-4 closes)

"""V2 slice-5 (WP9): snapshots / backup / restore / semantic rollback.

Contract: snapshot records (ledger head + schema + counts + path),
file-level backup + integrity-verified restore (staging only), and
SEMANTIC rollback (append-only: claims flagged ROLLED_BACK with the
snapshot id + reason; history preserved, never rewritten). Schema v9."""

import pytest
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def claim_event(claim_id="c1", statement="The retry loop caused the outage",
                claim_type="causal-analysis"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": "mission-1",
         "statement": statement, "claim_type": claim_type,
         "confidence": 0.8, "fresh_until": "2026-12-31T00:00:00Z",
         "source": {"run_id": "run-1", "event_id": f"event-{claim_id}"}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}")


def test_snapshot_created_projects_row(tmp_path):
    from jarvis_foundry.knowledge_snapshots import create_snapshot
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    with db.connect() as connection:
        head = connection.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0]
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    assert result["snapshot_id"].startswith("snap-")
    row = db.row("knowledge_snapshots", "snapshot_id", result["snapshot_id"])
    assert row["head_event_id"] == head
    assert row["schema_version"] == 9
    assert row["claims_count"] == 1
    assert result["path"].exists()


def test_snapshot_backup_file_is_restorable(tmp_path):
    from jarvis_foundry.knowledge_snapshots import (create_snapshot,
                                                    verify_snapshot)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    assert verify_snapshot(db, result["snapshot_id"]) is True


def test_verify_snapshot_detects_payload_tampering(tmp_path):
    """Regression for verifier deleg_9ecde3af F2: content-level corruption
    (payload byte flips that pass quick_check) must fail verification via
    the content sha256 cross-check."""
    from jarvis_foundry.knowledge_snapshots import (create_snapshot,
                                                    verify_snapshot)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    assert verify_snapshot(db, result["snapshot_id"]) is True
    # flip bytes inside a payload region (claim statement text)
    data = bytearray(result["path"].read_bytes())
    marker = b"The retry loop caused the outage"
    idx = data.find(marker)
    assert idx >= 0, "payload marker not found"
    data[idx] = ord("X") if data[idx] != ord("X") else ord("Y")
    result["path"].write_bytes(bytes(data))
    assert verify_snapshot(db, result["snapshot_id"]) is False
    # structural damage also fails
    result["path"].write_bytes(result["path"].read_bytes()[: len(result["path"].read_bytes()) // 2])
    assert verify_snapshot(db, result["snapshot_id"]) is False


def test_restore_from_snapshot_reopens_readonly(tmp_path):
    from jarvis_foundry.knowledge_snapshots import (create_snapshot,
                                                    open_snapshot_readonly)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event("c1"))
    db.append(claim_event("c2", "The DNS cache was stale"))
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    # mutate the live DB AFTER the snapshot
    db.append(claim_event("c3", "A third claim after the snapshot"))
    snapshot_db = open_snapshot_readonly(result["path"])
    with snapshot_db.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
        version = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
    assert count == 2  # snapshot content, not the live post-snapshot state
    assert version == 9
    snapshot_db.close()


def test_semantic_rollback_flags_claims_never_rewrites(tmp_path):
    from jarvis_foundry.knowledge_snapshots import (create_snapshot,
                                                    semantic_rollback)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event("c1"))
    db.append(claim_event("c2", "The DNS cache was stale"))
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    semantic_rollback(db, "mission-1", result["snapshot_id"],
                      ["c1", "c2"], "superseded by the packet-capture analysis")
    row = db.row("knowledge_claims", "claim_id", "c1")
    assert row["status"] == "ROLLED_BACK"
    assert result["snapshot_id"] in row["rolled_back_by"]
    # history preserved: the claim ROW still exists, the statement intact
    assert row["statement"] == "The retry loop caused the outage"


def test_rollback_requires_existing_snapshot(tmp_path):
    from jarvis_foundry.knowledge_snapshots import semantic_rollback
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event("c1"))
    with pytest.raises(Exception):
        semantic_rollback(db, "mission-1", "snap-does-not-exist",
                          ["c1"], "test")
    assert db.row("knowledge_claims", "claim_id", "c1")["status"] == "ACCEPTED"


def test_rebuild_reproduces_snapshots(tmp_path):
    from jarvis_foundry.knowledge_snapshots import (create_snapshot,
                                                    semantic_rollback)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event("c1"))
    result = create_snapshot(db, "mission-1", "knowledge", tmp_path / "snap")
    semantic_rollback(db, "mission-1", result["snapshot_id"], ["c1"], "reason")
    before = db.all("knowledge_snapshots")
    db.rebuild_projections()
    after = db.all("knowledge_snapshots")
    assert len(after) == len(before) == 1
    assert db.row("knowledge_claims", "claim_id", "c1")["status"] == "ROLLED_BACK"


def test_schema_version_matches_code(tmp_path):
    from jarvis_foundry.migrations import SCHEMA_VERSION
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    with db.connect() as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION == 9
