"""V2 slice-2 (WP9): knowledge retrieval + bounded context manifests.

Contract (per the slice-2 design): deterministic bounded retrieval over
the epistemic core (ACCEPTED + fresh by default, claim_type filter,
confidence ordering, limit cap); context manifests with objective-driven
claim_type hints, conflict listing, explicit STALE section, explicit
max_chars truncation, and an auditable manifest event whose projection
reproduces deterministically under rebuild."""
import json

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.knowledge import (canonicalize_statement,
                                      expand_contractions, split_negation,
                                      contradicts)


def claim(claim_id: str, statement: str, claim_type: str = "fact",
          confidence: float = 0.9, fresh_until: str = "2026-12-31T00:00:00Z",
          mission_id: str = "mission-1", run: str = "run-1"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": claim_type,
         "confidence": confidence, "fresh_until": fresh_until,
         "source": {"run_id": run, "event_id": f"event-{claim_id}"}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}",
    )


def seed(db: FoundryDatabase) -> None:
    db.append(claim("c1", "The outage was caused by a network failure",
                    claim_type="causal-analysis", confidence=0.8))
    db.append(claim("c2", "The outage was caused by a DNS failure",
                    claim_type="causal-analysis", confidence=0.6))
    db.append(claim("c3", "The fix is deployed", claim_type="fact",
                    confidence=0.95))
    # an expired claim
    db.append(claim("c4", "The old incident is resolved", claim_type="fact",
                    confidence=0.5,
                    fresh_until="2026-01-01T00:00:00Z"))
    # a contradiction pair (c5 vs c6)
    db.append(claim("c5", "The agent runs nightly", claim_type="fact",
                    confidence=0.9))
    db.append(claim("c6", "The agent doesn't run nightly", claim_type="fact",
                    confidence=0.9))


def test_retrieve_fresh_accepted_only(tmp_path):
    from jarvis_foundry.knowledge_retrieval import retrieve
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        rows = retrieve(connection, "mission-1")
    ids = [r["claim_id"] for r in rows]
    assert "c4" not in ids  # expired -> not active
    assert "c5" not in ids or "c6" not in ids  # contradicted pair not both active
    assert "c1" in ids and "c3" in ids
    assert all(r["status"] == "ACCEPTED" for r in rows)


def test_retrieve_type_filter_ordering_and_limit(tmp_path):
    from jarvis_foundry.knowledge_retrieval import retrieve
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        rows = retrieve(connection, "mission-1", claim_type="causal-analysis",
                        limit=1)
    assert len(rows) == 1
    assert rows[0]["claim_id"] == "c1"  # highest confidence first


def test_manifest_objective_hints_select_section(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        manifest = build_context_manifest(
            connection, "mission-1",
            "Investigate the causal-analysis of the outage")
    assert manifest.objective == "Investigate the causal-analysis of the outage"
    active_ids = [c["claim_id"] for c in manifest.active_claims]
    assert "c1" in active_ids and "c2" in active_ids  # hint selected the type
    assert manifest.active_claims and all(
        c["claim_type"] == "causal-analysis" for c in manifest.active_claims)


def test_manifest_lists_conflicts_with_both_ids(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        manifest = build_context_manifest(connection, "mission-1", "anything")
    assert len(manifest.conflicts) >= 1
    pair = {manifest.conflicts[0]["claim_a_id"],
            manifest.conflicts[0]["claim_b_id"]}
    assert pair == {"c5", "c6"}


def test_manifest_stale_section_never_drops(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        manifest = build_context_manifest(connection, "mission-1", "anything")
    stale_ids = [c["claim_id"] for c in manifest.stale_claims]
    assert "c4" in stale_ids  # expired but listed, never dropped
    assert all(c["claim_id"] != "c4" for c in manifest.active_claims)


def test_manifest_truncation_is_explicit(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        tiny = build_context_manifest(connection, "mission-1", "anything",
                                      max_chars=120)
    assert tiny.truncated is True
    assert tiny.active_count == 0 or tiny.truncated
    with db.connect() as connection:
        big = build_context_manifest(connection, "mission-1", "anything",
                                     max_chars=100000)
    assert big.truncated is False


def test_manifest_conflicts_are_mission_scoped(tmp_path):
    """Regression for verifier deleg_cd7da327 S2-g: contradiction pairs of
    OTHER missions must never leak into a manifest's conflicts section
    (previously scoped only by claim_type overlap)."""
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)  # mission-1: c5/c6 contradiction pair (fact type)
    # mission-2 pair with the SAME claim_type (fact) — the leak class
    db.append(claim("m2a", "The sky is blue", claim_type="fact",
                    mission_id="mission-2", run="run-2"))
    db.append(claim("m2b", "The sky isn't blue", claim_type="fact",
                    mission_id="mission-2", run="run-2"))
    with db.connect() as connection:
        manifest = build_context_manifest(connection, "mission-1", "anything")
    pair_ids = {c["claim_a_id"] for c in manifest.conflicts} | \
        {c["claim_b_id"] for c in manifest.conflicts}
    assert "m2a" not in pair_ids and "m2b" not in pair_ids
    assert "c5" in pair_ids and "c6" in pair_ids
    # and the mission-2 manifest sees only its own pair
    with db.connect() as connection:
        manifest2 = build_context_manifest(connection, "mission-2", "anything")
    pair2 = {c["claim_a_id"] for c in manifest2.conflicts} | \
        {c["claim_b_id"] for c in manifest2.conflicts}
    assert pair2 == {"m2a", "m2b"}


def test_manifest_event_projection_and_rebuild(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        manifest = build_context_manifest(connection, "mission-1",
                                          "step-1 objective")
    db.append(Event.create(
        "knowledge.context_manifest_generated",
        {"manifest_id": "manifest-1", "mission_id": "mission-1",
         "step_id": "step-1", "objective_hash": manifest.objective_hash,
         "active_count": manifest.active_count,
         "stale_count": len(manifest.stale_claims),
         "conflict_count": len(manifest.conflicts),
         "truncated": int(manifest.truncated),
         "manifest_hash": manifest.manifest_hash},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.context_manifest_generated:manifest-1"))
    row = db.row("knowledge_manifests", "manifest_id", "manifest-1")
    assert row["manifest_hash"] == manifest.manifest_hash
    assert row["active_count"] == manifest.active_count
    db.rebuild_projections()
    row2 = db.row("knowledge_manifests", "manifest_id", "manifest-1")
    assert row2["manifest_hash"] == manifest.manifest_hash


def test_manifest_determinism(tmp_path):
    from jarvis_foundry.knowledge_retrieval import build_context_manifest
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed(db)
    with db.connect() as connection:
        first = build_context_manifest(connection, "mission-1",
                                       "same objective")
    with db.connect() as connection:
        second = build_context_manifest(connection, "mission-1",
                                        "same objective")
    # determinism contract: the manifest HASH + content are stable;
    # generated_at is a wall-clock field and legitimately differs
    assert first.manifest_hash == second.manifest_hash
    first_dict = first.as_dict()
    second_dict = second.as_dict()
    first_dict.pop("generated_at")
    second_dict.pop("generated_at")
    assert first_dict == second_dict
