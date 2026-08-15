"""V8 hybrid memory — recall, blocks, procedures.

Traceability: lab test_recall_and_context (recall metadata, stale flag,
irrelevant exclusion), test_curation_defense_versioning (duplicates),
audit L2 (malformed expiry must not crash recall).
"""
from __future__ import annotations

import pytest

from jarvis_foundry.hybrid_memory import BlockBudgetExceeded, ReadOnlyBlock
from v8_harness import lab  # noqa: F401


def test_recall_returns_metadata_and_excludes_irrelevant(lab):
    lab.promote("supervisor exits with 0xC000013A: console control handler fixed it",
                scope="project", source_ids=["log"], evidence_ids=["ev-log"])
    lab.promote("tomatoes need watering every other day", scope="personal")
    hits = lab.recall.recall("supervisor exit 0xC000013A", scope="project")
    assert hits
    top = hits[0]
    assert top["memory_type"] == "episodic"
    assert "ev-log" in top["evidence_refs"]
    assert top["freshness"] in ("FRESH", "STALE")
    assert "tomato" not in top["summary"]
    assert len(top["summary"]) <= 220


def test_stale_memory_flagged_but_returned(lab):
    mid = lab.promote("port 3000 is occupied by another service")
    with lab.db.connect() as conn:
        conn.execute("UPDATE tier_c_memories SET expires_at='2000-01-01T00:00:00+00:00' "
                     "WHERE memory_id=?", (mid,))
    hits = lab.recall.recall("port 3000 occupied")
    assert any(h["memory_id"] == mid and h["freshness"] == "STALE" for h in hits)


def test_malformed_expiry_degrades_not_crashes(lab):
    """Audit L2: a naive/malformed expires_at must not crash recall."""
    mid = lab.promote("wal sidecar checkpoint before file restore")
    with lab.db.connect() as conn:
        conn.execute("UPDATE tier_c_memories SET expires_at='2000-01-01T00:00:00' "
                     "WHERE memory_id=?", (mid,))
    hits = lab.recall.recall("wal checkpoint restore")  # must not raise
    assert any(h["memory_id"] == mid for h in hits)


def test_duplicate_gate_blocks_promotion_and_preserves_state(lab):
    """Near-identical experience cannot be promoted as a second record; the
    first record's provenance is untouched."""
    a = lab.promote("Bare python is broken in the Hermes shell",
                    source_ids=["s1"], evidence_ids=["e1"])
    b = lab.observe("Bare python is broken in the Hermes shell environment",
                    source_ids=["s2"], evidence_ids=["e2"])
    lab.curation.propose_candidate(b, actor="worker-1")
    with pytest.raises(ValueError, match="duplicate_of"):
        lab.curation.validate_and_accept(b, placement="CORE")
    # the gate blocked it before acceptance
    assert lab.memories.get(b)["state"] != "CORE"
    assert lab.memories.get(a)["state"] == "CORE"
    assert lab.memories.get(a)["source_ids"] == ["s1"]


def test_consolidation_merges_legacy_duplicates_preserving_history(lab):
    """Consolidation merges co-accepted near-duplicates (legacy/imported
    records), preserving provenance and revision history."""
    from jarvis_foundry.events import Event

    def _accept(content: str, source: str, evidence: str) -> str:
        mid = lab.observe(content, source_ids=[source], evidence_ids=[evidence])
        lab.db.append(Event.create(
            "memory.validation_passed",
            {"memory_id": mid, "verdict": {"passed": True}, "validated_by": "import"},
            source_type="memory", source_id="import",
            idempotency_key=f"v:{mid}"))
        lab.db.append(Event.create(
            "memory.transition",
            {"memory_id": mid, "from": "VALIDATING", "to": "ACCEPTED_EXPERIENCE",
             "actor": "import", "reason": "legacy import", "content_before": content},
            source_type="memory", source_id="import",
            idempotency_key=f"t1:{mid}"))
        lab.db.append(Event.create(
            "memory.transition",
            {"memory_id": mid, "from": "ACCEPTED_EXPERIENCE", "to": "CORE",
             "actor": "import", "reason": "legacy import", "content_before": content},
            source_type="memory", source_id="import",
            idempotency_key=f"t2:{mid}"))
        return mid

    a = _accept("Bare python is broken in the Hermes shell", "s1", "e1")
    b = _accept("Bare python is broken in the Hermes shell environment", "s2", "e2")
    merged = lab.curation.consolidate_duplicates([a, b], actor="curation")
    assert merged == [a] or merged == [b]
    keeper = merged[0]
    other = a if b == keeper else b
    assert lab.memories.get(other)["state"] == "SUPERSEDED"
    assert lab.memories.get(other)["supersedes"] == keeper
    assert lab.memories.get(keeper) is not None  # nothing deleted
    assert lab.memories.revisions(other)  # history preserved


def test_blocks_budget_and_read_only(lab):
    assert "owner" in lab.blocks.labels()
    lab.blocks.replace("current_projects", "x" * 100, actor="w", reason="test")
    with pytest.raises(BlockBudgetExceeded):
        lab.blocks.append("current_projects", "y" * 5000, actor="w", reason="overflow")
    with pytest.raises(ReadOnlyBlock):
        lab.blocks.replace("owner", "not allowed", actor="w", reason="test")
    occupancy = lab.blocks.occupancy()
    assert occupancy["current_projects"]["chars"] == 100
    assert lab.blocks.revisions("current_projects")


def test_procedure_search_and_lifecycle(lab):
    pid = lab.procedures.add(
        "powershell-quoting", "native quoting for repo paths with spaces",
        "Use native PowerShell absolute quoting for repository paths containing spaces.",
        ["spaces", "quoting", "powershell"])
    hits = lab.procedures.search("paths with spaces in the repository")
    assert any(h["procedure_id"] == pid for h in hits)
    lab.procedures.set_state(pid, "STALE", actor="curation", reason="superseded")
    assert lab.procedures.get(pid)["state"] == "STALE"
    assert not any(h["procedure_id"] == pid
                   for h in lab.procedures.search("spaces in repository"))
