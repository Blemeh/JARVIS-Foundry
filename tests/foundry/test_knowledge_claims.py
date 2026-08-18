"""V2 slice-1 (WP9): knowledge claims — epistemic core tests.

Contract (per docs/planning/CURRENT_WORK_PACKAGE.yaml + the slice-1
blueprint): claims with provenance/confidence/freshness, deterministic
contradiction detection (canonicalized negation fingerprints), supersede
with validation, dedup/conflict on re-delivery, bounded queries, and
deterministic rebuilds."""
import json

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def claim_event(claim_id: str = "claim-1", statement: str = "The sky is blue",
                claim_type: str = "fact", confidence: float = 0.9,
                fresh_until: str = "2026-12-31T00:00:00Z",
                mission_id: str = "mission-1", source_run: str = "run-1",
                source_event: str = "event-1"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": claim_type,
         "confidence": confidence, "fresh_until": fresh_until,
         "source": {"run_id": source_run, "event_id": source_event}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}",
    )


def negation_event(claim_id: str = "claim-2", statement: str = "The sky is not blue",
                   claim_type: str = "fact", mission_id: str = "mission-1"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": claim_type,
         "confidence": 0.95, "fresh_until": "2026-12-31T00:00:00Z",
         "source": {"run_id": "run-2", "event_id": "event-2"}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}",
    )


def test_negation_variant_family_no_false_negatives(tmp_path):
    """Regression for verifier deleg_642b84b2 K1: the contraction family
    and whitespace/punctuation/case variants MUST contradict the positive
    claim (punctuation was previously stripped before token matching,
    making 'isn't' tokens unreachable and apostrophe-free spellings
    removing the copula)."""
    positive = "The sky is blue"
    negated_variants = [
        "The sky is not blue",
        "The sky is NOT blue",
        "The sky is not   blue",
        "The sky is not blue.",
        "The sky isn't blue",
        "The sky ISN'T blue",
        "The sky isn't   blue.",
        "The sky isnt blue",
        "The sky is not blue!!",
    ]
    for variant in negated_variants:
        db = FoundryDatabase(tmp_path / f"foundry-{abs(hash(variant)) % 100000}.db")
        db.initialize()
        db.append(claim_event(claim_id="p", statement=positive))
        db.append(negation_event(claim_id="n", statement=variant))
        rows = db.all("knowledge_contradictions")
        assert len(rows) == 1, f"no contradiction for: {variant!r}"
        assert db.row("knowledge_claims", "claim_id", "p")["status"] == \
            "CONTRADICTED", f"positive not flagged for: {variant!r}"

    # non-contradicting near-siblings must stay clean (no false positives)
    for sibling in ("The sky is green", "The sky is not green",
                    "The ocean is blue"):
        db = FoundryDatabase(tmp_path / f"foundry-{abs(hash(sibling)) % 100000}.db")
        db.initialize()
        db.append(claim_event(claim_id="p", statement=positive))
        db.append(negation_event(claim_id="n", statement=sibling))
        assert db.count("knowledge_contradictions") == 0, \
            f"false positive for: {sibling!r}"


def test_do_support_family_no_false_negatives(tmp_path):
    """Regression for verifier deleg_bb94028c R2: do-support negations
    must contradict the plain 3rd-person claim ("The system doesn't work"
    vs "The system works" — exact-core equality previously left the
    auxiliary and verb inflection unnormalized)."""
    pairs = [
        ("The system works", "The system doesn't work"),
        ("The system works", "The system does not work"),
        ("The system works", "The system doesnt work"),
        ("The system fails", "The system doesn't fail"),
        ("The build passes", "The build doesn't pass"),
        ("The agent runs", "The agent doesn't run"),
        ("She has a car", "She doesn't have a car"),
    ]
    for i, (positive, variant) in enumerate(pairs):
        db = FoundryDatabase(tmp_path / f"foundry-pos-{i}.db")
        db.initialize()
        db.append(claim_event(claim_id="p", statement=positive))
        db.append(negation_event(claim_id="n", statement=variant))
        rows = db.all("knowledge_contradictions")
        assert len(rows) == 1, f"no contradiction: {positive!r} vs {variant!r}"
        assert db.row("knowledge_claims", "claim_id", "p")["status"] == \
            "CONTRADICTED"

    # near-siblings with a DIFFERENT verb must stay clean
    for i, sibling in enumerate(("The system starts", "The system works hard",
                    "The system worked")):
        db = FoundryDatabase(tmp_path / f"foundry-sib-{i}.db")
        db.initialize()
        db.append(claim_event(claim_id="p", statement="The system works"))
        db.append(negation_event(claim_id="n", statement=sibling))
        assert db.count("knowledge_contradictions") == 0, \
            f"false positive: {sibling!r}"


def test_claim_recorded_projects_row_with_provenance(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    row = db.row("knowledge_claims", "claim_id", "claim-1")
    assert row["status"] == "ACCEPTED"
    assert row["statement"] == "The sky is blue"
    assert row["claim_type"] == "fact"
    assert abs(row["confidence"] - 0.9) < 1e-9
    assert row["fresh_until"] == "2026-12-31T00:00:00Z"
    assert row["source_run_id"] == "run-1"
    assert row["source_event_id"] == "event-1"


def test_duplicate_identical_redelivery_deduped(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(claim_event())  # same idempotency key + payload
    assert db.count("knowledge_claims") == 1


def test_duplicate_changed_payload_conflicts(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    changed = claim_event(statement="The sky is actually green")
    with pytest.raises(Exception) as exc:
        db.append(changed)
    assert "conflict" in str(exc.value).lower() or "EventConflict" in str(exc.value)


def test_negation_pair_detected_at_record_time(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(negation_event())
    rows = db.all("knowledge_contradictions")
    assert len(rows) == 1
    contradiction = rows[0]
    assert {contradiction["claim_a_id"], contradiction["claim_b_id"]} == \
        {"claim-1", "claim-2"}
    # both claims flagged
    assert db.row("knowledge_claims", "claim_id", "claim-1")["status"] == \
        "CONTRADICTED"
    assert db.row("knowledge_claims", "claim_id", "claim-2")["status"] == \
        "CONTRADICTED"


def test_contradiction_sweep_detects_ordering_case(tmp_path):
    """The negation arrives BEFORE the claim; the sweep must still flag it."""
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(negation_event(claim_id="claim-2"))
    db.append(claim_event(claim_id="claim-1"))
    # at-record-time detection catches the second event too (pair symmetric)
    assert db.count("knowledge_contradictions") == 1
    assert db.row("knowledge_claims", "claim_id", "claim-1")["status"] == \
        "CONTRADICTED"


def test_supersede_requires_existing_claim(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": "claim-3", "mission_id": "mission-1",
         "statement": "The sky is teal", "claim_type": "fact",
         "confidence": 0.99, "fresh_until": "2026-12-31T00:00:00Z",
         "source": {"run_id": "run-3", "event_id": "event-3"}},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_recorded:claim-3"))
    db.append(Event.create(
        "knowledge.claim_superseded",
        {"claim_id": "claim-1", "superseded_by": "claim-3",
         "reason": "newer evidence"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_superseded:claim-1"))
    assert db.row("knowledge_claims", "claim_id", "claim-1")["status"] == \
        "SUPERSEDED"
    assert db.row("knowledge_claims", "claim_id", "claim-1")["superseded_by"] \
        == "claim-3"
    # superseding a missing claim must fail atomically
    with pytest.raises(Exception):
        db.append(Event.create(
            "knowledge.claim_superseded",
            {"claim_id": "does-not-exist", "superseded_by": "claim-3",
             "reason": "test"},
            source_type="test", source_id="test-suite",
            idempotency_key="knowledge.claim_superseded:does-not-exist"))
    assert db.row("knowledge_claims", "claim_id", "claim-1")["status"] == \
        "SUPERSEDED"  # prior state untouched


def test_rebuild_reproduces_rows_deterministically(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(negation_event())
    before = db.all("knowledge_contradictions")
    db.rebuild_projections()
    after = db.all("knowledge_contradictions")
    assert len(after) == len(before) == 1
    assert db.row("knowledge_claims", "claim_id", "claim-1")["status"] == \
        "CONTRADICTED"


def test_bounded_queries(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(negation_event())
    db.append(claim_event(claim_id="claim-3", statement="Coffee is hot",
                          claim_type="preference"))
    # fresh accepted claims only (claim-1/2 contradicted)
    assert db.count("knowledge_claims") == 3
    fresh = db.all("knowledge_claims", status="ACCEPTED")
    assert len(fresh) == 1 and fresh[0]["claim_id"] == "claim-3"
    contradictions = db.all("knowledge_contradictions")
    assert len(contradictions) == 1
