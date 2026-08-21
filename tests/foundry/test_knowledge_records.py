"""V2 Knowledge Brain typed records — WP-24H-V2KB slice tests.

Contract (WORK_PACKAGE.md §4): first-class typed epistemic records
(Observation/Evidence/Claim/Belief/Decision/Unknown), immutable evidence
links with explicit unsupported marking, typed entities, wider bounded
retrieval dimensions — all riding the EXISTING ledger/projection stack
(no second engine). TDD: every test here was observed RED before the
implementation existed.
"""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.knowledge_retrieval import retrieve

RECORD_KINDS = ("OBSERVATION", "EVIDENCE", "CLAIM", "BELIEF", "DECISION",
                "UNKNOWN")
ENTITY_TYPES = ("goal", "project", "person", "organization", "system")


def claim_event(claim_id="c1", statement="The deploy pipeline is green",
                kind="CLAIM", confidence=0.8,
                fresh_until="2027-01-01T00:00:00Z", mission_id="mission-kb",
                source_run="run-kb", source_event="ev-kb"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": "fact",
         "record_kind": kind,
         "confidence": confidence, "fresh_until": fresh_until,
         "source": {"run_id": source_run, "event_id": source_event}},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}")


def evidence_link_event(claim_id="c1", evidence_id="evd-1",
                        payload_hash="ab" * 32):
    return Event.create(
        "knowledge.evidence_linked",
        {"claim_id": claim_id, "evidence_id": evidence_id,
         "payload_hash": payload_hash},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key=f"knowledge.evidence_linked:{claim_id}:{evidence_id}")


# ---------------------------------------------------------------- typed records

@pytest.mark.parametrize("kind", RECORD_KINDS)
def test_all_six_record_kinds_persist(tmp_path, kind):
    """Every canonical record kind persists with kind + evidence status."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    cid = f"c-{kind.lower()}"
    db.append(claim_event(claim_id=cid, kind=kind))
    row = db.row("knowledge_claims", "claim_id", cid)
    assert row["record_kind"] == kind


def test_claim_without_evidence_is_unsupported(tmp_path):
    """A claim with no evidence links carries an explicit UNSUPPORTED state."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(claim_event(claim_id="c-uns"))
    row = db.row("knowledge_claims", "claim_id", "c-uns")
    assert row["evidence_status"] == "UNSUPPORTED"


def test_legacy_style_claim_defaults_to_claim_kind_supported_shape(tmp_path):
    """Pre-slice payloads (no record_kind field) still project: kind
    defaults to CLAIM, and the row exposes the evidence-status column."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    legacy = Event.create(
        "knowledge.claim_recorded",
        {"claim_id": "c-old", "mission_id": "mission-kb",
         "statement": "Legacy shape", "claim_type": "fact",
         "confidence": 0.5, "fresh_until": None,
         "source": {"run_id": "r", "event_id": "e"}},
        source_type="test", source_id="legacy",
        idempotency_key="knowledge.claim_recorded:c-old")
    db.append(legacy)
    row = db.row("knowledge_claims", "claim_id", "c-old")
    assert row["record_kind"] == "CLAIM"
    assert row["evidence_status"] in ("UNSUPPORTED", "SUPPORTED")


def test_content_hash_recorded_for_every_claim(tmp_path):
    """Provenance integrity: each claim stores the sha256 of its content."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(claim_event(claim_id="c-hash"))
    row = db.row("knowledge_claims", "claim_id", "c-hash")
    assert isinstance(row["content_sha256"], str) and len(row["content_sha256"]) == 64


# ---------------------------------------------------------------- evidence links

def test_linking_evidence_flips_unsupported_to_supported(tmp_path):
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(claim_event(claim_id="c-ev"))
    assert db.row("knowledge_claims", "claim_id", "c-ev")[
        "evidence_status"] == "UNSUPPORTED"
    db.append(evidence_link_event(claim_id="c-ev", evidence_id="evd-1"))
    assert db.row("knowledge_claims", "claim_id", "c-ev")[
        "evidence_status"] == "SUPPORTED"
    links = db.all("knowledge_claim_evidence")
    assert len(links) == 1
    assert links[0]["claim_id"] == "c-ev"
    assert links[0]["evidence_id"] == "evd-1"


def test_evidence_links_are_immutable_append_only(tmp_path):
    """Identical re-delivery dedupes; there is NO event type that deletes
    or mutates an evidence link (append-only history)."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(claim_event(claim_id="c-im"))
    link = evidence_link_event(claim_id="c-im", evidence_id="evd-x")
    applied1 = db.append(link)
    applied2 = db.append(evidence_link_event(claim_id="c-im",
                                             evidence_id="evd-x"))
    assert len(db.all("knowledge_claim_evidence")) == 1
    assert applied2[1] is False  # deduped, not re-applied
    # changed payload under the same key must CONFLICT, never rewrite
    flipped = Event.create(
        "knowledge.evidence_linked",
        {"claim_id": "c-im", "evidence_id": "evd-x",
         "payload_hash": "cd" * 32},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key="knowledge.evidence_linked:c-im:evd-x")
    with pytest.raises(EventConflict):
        db.append(flipped)
    # ledger vocabulary: no destructive evidence event types exist in the
    # knowledge.* namespace (append-only links)
    types = {r[0] for r in db.connect().execute(
        "SELECT DISTINCT event_type FROM events").fetchall()}
    bad = [t for t in types if t.startswith("knowledge.")
           and "evidence" in t and t != "knowledge.evidence_linked"]
    assert bad == []


def test_link_requires_existing_claim(tmp_path):
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    with pytest.raises(ValueError):
        db.append(evidence_link_event(claim_id="ghost"))


def test_every_claim_has_evidence_or_explicit_unsupported(tmp_path):
    """Roadmap exit criterion: invariant sweep over a mixed population."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    for i in range(4):
        db.append(claim_event(claim_id=f"c-{i}"))
    db.append(evidence_link_event(claim_id="c-0", evidence_id="e0"))
    db.append(evidence_link_event(claim_id="c-2", evidence_id="e2"))
    rows = db.all("knowledge_claims")
    assert len(rows) == 4
    for row in rows:
        assert row["evidence_status"] in ("SUPPORTED", "UNSUPPORTED")
    supported = {r["claim_id"] for r in rows
                 if r["evidence_status"] == "SUPPORTED"}
    assert supported == {"c-0", "c-2"}


# ---------------------------------------------------------------- entities

@pytest.mark.parametrize("etype", ENTITY_TYPES)
def test_typed_entities_goal_project_person_org_system(tmp_path, etype):
    db = FoundryDatabase(tmp_path / f"f-{etype}.db")
    db.initialize()
    eid = f"ent-{etype}"
    db.append(Event.create(
        "knowledge.entity_registered",
        {"entity_id": eid, "mission_id": "mission-kb",
         "canonical_name": f"Entity {etype}", "aliases": [],
         "entity_type": etype},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key=f"knowledge.entity_registered:{eid}"))
    assert db.row("knowledge_entities", "entity_id", eid)[
        "entity_type"] == etype


def test_entity_type_defaults_and_rejects_unknown(tmp_path):
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(Event.create(
        "knowledge.entity_registered",
        {"entity_id": "ent-def", "mission_id": "mission-kb",
         "canonical_name": "Default", "aliases": []},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key="knowledge.entity_registered:ent-def"))
    assert db.row("knowledge_entities", "entity_id", "ent-def")[
        "entity_type"] == "system"
    from jarvis_foundry.knowledge_entities import resolve_entity
    assert resolve_entity(db.connect(), "default") == "ent-def"
    with pytest.raises(ValueError):
        db.append(Event.create(
            "knowledge.entity_registered",
            {"entity_id": "ent-bad", "mission_id": "mission-kb",
             "canonical_name": "Bad", "aliases": [],
             "entity_type": "galaxy"},
            source_type="test", source_id="wp24h-v2kb",
            idempotency_key="knowledge.entity_registered:ent-bad"))


def test_typed_lookup_helpers(tmp_path):
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    for i, etype in enumerate(ENTITY_TYPES):
        eid = f"T{i}-{etype}"
        db.append(Event.create(
            "knowledge.entity_registered",
            {"entity_id": eid, "mission_id": "mission-kb",
             "canonical_name": etype.capitalize(), "aliases": [],
             "entity_type": etype},
            source_type="test", source_id="wp24h-v2kb",
            idempotency_key=f"knowledge.entity_registered:{eid}"))
    from jarvis_foundry.knowledge_entities import entities_of_type
    people = entities_of_type(db.connect(), "person")
    assert people == ["T2-person"]
    goals = entities_of_type(db.connect(), "goal")
    assert goals == ["T0-goal"]


# ---------------------------------------------------------------- retrieval

def _seed_retrieval_db(tmp_path):
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    specs = [
        ("r1", "Alpha pipeline works", "OBSERVATION", 0.9,
         "2027-06-01T00:00:00Z", "src-a"),
        ("r2", "Beta pipeline works", "BELIEF", 0.4,
         "2027-06-01T00:00:00Z", "src-b"),
        ("r3", "Gamma pipeline works", "DECISION", 0.7,
         "2026-01-01T00:00:00Z", "src-a"),   # expired freshness
        ("r4", "Delta pipeline works", "UNKNOWN", 0.95,
         "2027-06-01T00:00:00Z", "src-c"),
    ]
    for cid, stmt, kind, conf, fresh, src in specs:
        db.append(claim_event(claim_id=cid, statement=stmt, kind=kind,
                              confidence=conf, fresh_until=fresh,
                              source_run=src))
    return db


def test_retrieve_filters_by_kind_confidence_time_source(tmp_path):
    db = _seed_retrieval_db(tmp_path)
    conn = db.connect()
    kinds = [r["record_kind"] for r in
             retrieve(conn, "mission-kb", record_kind="BELIEF")]
    assert kinds == ["BELIEF"]
    high = [r["claim_id"] for r in
            retrieve(conn, "mission-kb", min_confidence=0.85)]
    assert high == ["r4", "r1"]
    band = {r["claim_id"] for r in
            retrieve(conn, "mission-kb", min_confidence=0.3,
                     max_confidence=0.8, fresh_only=False)}
    assert band == {"r2", "r3"}
    window = {r["claim_id"] for r in
              retrieve(conn, "mission-kb",
                       recorded_from="2026-06-01T00:00:00Z",
                       fresh_only=False)}
    assert window == {"r1", "r2", "r3", "r4"}
    future_only = retrieve(conn, "mission-kb",
                           recorded_from="2999-01-01T00:00:00Z",
                           fresh_only=False)
    assert future_only == []
    src_a = [r["claim_id"] for r in
             retrieve(conn, "mission-kb", source_run="src-a")]
    assert src_a == ["r1"]


def test_retrieve_is_deterministic_and_bounded(tmp_path):
    db = _seed_retrieval_db(tmp_path)
    conn = db.connect()
    first = retrieve(conn, "mission-kb", limit=2)
    again = retrieve(conn, "mission-kb", limit=2)
    assert [r["claim_id"] for r in first] == [r["claim_id"] for r in again]
    assert len(first) == 2
    for item in first:
        assert item["source_run_id"]  # provenance-aware rows


def test_retrieve_by_entity(tmp_path):
    db = _seed_retrieval_db(tmp_path)
    db.append(Event.create(
        "knowledge.entity_registered",
        {"entity_id": "ent-pipe", "mission_id": "mission-kb",
         "canonical_name": "pipeline", "aliases": [],
         "entity_type": "system"},
        source_type="test", source_id="wp24h-v2kb",
        idempotency_key="knowledge.entity_registered:ent-pipe"))
    for cid in ("r1", "r3"):
        db.append(Event.create(
            "knowledge.claim_entity_linked",
            {"claim_id": cid, "entity_id": "ent-pipe"},
            source_type="test", source_id="wp24h-v2kb",
            idempotency_key=f"link:{cid}:ent-pipe"))
    linked = {r["claim_id"] for r in
              retrieve(db.connect(), "mission-kb", entity_id="ent-pipe",
                       fresh_only=False)}
    assert linked == {"r1", "r3"}


def test_conflicting_claims_remain_visible_with_history(tmp_path):
    """Mandatory: conflicts stay VISIBLE — both statements intact, both
    flagged, contradiction row recorded, nothing deleted."""
    db = FoundryDatabase(tmp_path / "f.db")
    db.initialize()
    db.append(claim_event(claim_id="pos",
                          statement="The service is up"))
    db.append(claim_event(claim_id="neg",
                          statement="The service is not up"))
    pos = db.row("knowledge_claims", "claim_id", "pos")
    neg = db.row("knowledge_claims", "claim_id", "neg")
    assert pos["statement"] == "The service is up"
    assert neg["statement"] == "The service is not up"
    assert pos["status"] == "CONTRADICTED" and neg["status"] == "CONTRADICTED"
    assert db.count("knowledge_contradictions") == 1
