"""V8 hybrid memory — regression tests for verifier-round repairs (F1-F6).

- F1/F2: block updates preserve FULL revision history (upsert, no cascade)
- F3: rebuild_projections is row-identical (deterministic TEXT revision ids)
- F4: compiler degrades to governance-only when tier_c tables are missing
- F5: evidence scripts only rewrite evidence files with --record
- F6: Tier C budget = remaining after Tier A/B (starved to zero cleanly)
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.reconcile import reconcile
from v8_harness import lab, make_lab  # noqa: F401


def test_block_history_grows_with_updates(lab):
    """F2: every block update appends a revision; nothing is destroyed."""
    lab.blocks.replace("current_projects", "project one", actor="w", reason="r1")
    lab.blocks.replace("current_projects", "project one and two", actor="w", reason="r2")
    lab.blocks.append("current_projects", " and three", actor="w", reason="r3")
    revisions = lab.blocks.revisions("current_projects")
    assert len(revisions) >= 4  # default + 3 updates, ALL preserved
    assert revisions[0]["value"] != revisions[-1]["value"]


def test_memory_revision_history_survives_rebuild(lab):
    """F3: rebuild_projections reproduces identical rows including TEXT
    revision ids (no AUTOINCREMENT drift)."""
    mid = lab.promote("revision history lesson")
    lab.memories.mark_stale(mid, actor="curation", reason="ttl")
    before = lab.memories.revisions(mid)
    before_rows = lab.memories.list()
    lab.db.rebuild_projections()
    after = lab.memories.revisions(mid)
    after_rows = lab.memories.list()
    assert [r["revision_id"] for r in before] == [r["revision_id"] for r in after]
    assert before_rows == after_rows


def test_reconcile_consistent_with_tier_c_activity(lab):
    """F1: reconcile must report consistent=True on a DB with tier_c
    activity (blocks updated multiple times, memories promoted)."""
    lab.blocks.replace("current_projects", "alpha", actor="w", reason="r1")
    lab.blocks.replace("current_projects", "alpha beta", actor="w", reason="r2")
    lab.promote("a useful lesson about wal checkpoints")
    lab.promote("another lesson about snapshot hashing")
    report = reconcile(lab.db)
    assert report.consistent is True, report.to_json()


def test_compiler_degrades_when_tier_c_missing(lab):
    """F4: with tier_c tables dropped, the compiler must still deliver
    governance (Tier C failure degrades memory, never control)."""
    with lab.db.transaction() as conn:
        for table in ("tier_c_memory_revisions", "tier_c_validations",
                      "tier_c_memories", "tier_c_block_revisions",
                      "tier_c_blocks", "tier_c_procedures"):
            conn.execute(f"DROP TABLE {table}")
    pkg = lab.compiler.compile("M-1", "executive", "anything")
    assert pkg.section("mandatory_governance") is not None
    assert any("tier_c_unavailable" in n for n in pkg.notes)


def test_compiler_tier_c_budget_is_remaining_after_tier_ab(lab):
    """F6: under a tiny budget, Tier C is fully dropped (recorded), never
    starves governance."""
    lab.promote("distinct lesson about console handlers", placement="CORE")
    pkg = lab.compiler.compile("M-1", "builder", "console handler",
                               budget_chars=120)
    assert pkg.section("mandatory_governance") is not None
    tier_c_sections = [k for k in ("core_memory", "recalled_experience", "procedures")
                       if pkg.section(k) is not None]
    assert not tier_c_sections or pkg.dropped  # either nothing fits or drops recorded


def test_evidence_scripts_need_record_flag(tmp_path: pathlib.Path):
    """F5: evidence files are only rewritten with --record; plain runs must
    not touch committed evidence."""
    repo = pathlib.Path(__file__).resolve().parents[2]
    evidence = repo / "evidence"
    if not evidence.exists():
        import pytest
        pytest.skip("evidence dir not present")
    target = evidence / "v8-benchmark-raw.json"
    if not target.exists():
        import pytest
        pytest.skip("evidence not committed yet")
    before = target.read_bytes()
    subprocess.run(
        [sys.executable, str(repo / "scripts" / "v8_benchmark.py")],
        cwd=repo, capture_output=True, timeout=120, check=False)
    after = target.read_bytes()
    assert before == after, "plain run rewrote committed evidence"
