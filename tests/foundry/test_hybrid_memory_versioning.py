"""V8 hybrid memory — git-backed versioning: rollback, divergence,
markerless fail-closed, rebuild. (Audit repairs H3/C9-4/L1.)

Traceability: lab test_git_rollback_history_preserved, audit H3 (silent
drift discard), C9-4 (markerless fail-open), L1 (manifest column
injection).
"""
from __future__ import annotations

import json

import pytest

from jarvis_foundry.hybrid_memory import DivergenceError
from v8_harness import lab  # noqa: F401


def test_rollback_history_preserved(lab):
    mid = lab.observe("revision A content", source_ids=["src"])
    sha_a = lab.git.commit_all("curation", "A")
    lab.memories.transition(mid, "CANDIDATE_MEMORY", actor="curation", reason="p1")
    lab.memories.transition(mid, "VALIDATING", actor="curation", reason="p1")
    lab.curation.validate_and_accept(mid, placement="CORE", actor="curation")
    sha_b = lab.git.commit_all("curation", "B (accepted)")
    # incorrect update C
    lab.memories.mark_stale(mid, actor="curation", reason="bad: stale")
    sha_c = lab.git.commit_all("curation", "C (bad)")
    # roll back to B — DB is ahead of B's marker, so force is the explicit
    # operator decision; the un-forced refusal is tested separately
    lab.git.revert_to(sha_b, actor="curation",
                      reason="rollback incorrect revision C", force=True)
    sha_after = lab.git.head()
    assert sha_after != sha_c
    # history A -> B -> C -> rollback all present
    shas = [e["sha"] for e in lab.git.log(limit=10)]
    assert sha_a in shas and sha_b in shas and sha_c in shas
    assert lab.memories.get(mid)["state"] == "CORE"


def test_divergence_detected_on_revert(lab):
    """Audit H3: revert must refuse to silently discard newer DB state."""
    mid = lab.observe("content A", source_ids=["src"])
    sha_a = lab.git.commit_all("curation", "A")
    lab.curation.propose_candidate(mid, actor="curation")
    lab.curation.validate_and_accept(mid, placement="CORE", actor="curation")
    with pytest.raises(DivergenceError):
        lab.git.revert_to(sha_a, actor="curation", reason="rollback")
    assert lab.memories.get(mid)["state"] == "CORE"  # drift preserved
    lab.git.revert_to(sha_a, actor="curation", reason="forced", force=True)
    assert lab.memories.get(mid)["state"] == "OBSERVED"


def test_markerless_target_fails_closed(lab):
    """Audit C9-4: a commit without _sync.json cannot be reverted without
    an explicit force."""
    mid = lab.observe("content", source_ids=["src"])
    lab.git.commit_all("curation", "A")
    # strip the marker from HEAD (simulates a pre-repair/external commit)
    (lab.git.git_dir / "_sync.json").unlink(missing_ok=True)
    lab.git._git("add", "-A")
    lab.git._git("commit", "-q", "--amend", "--no-edit")
    target = lab.git.head()
    lab.memories.transition(mid, "CANDIDATE_MEMORY", actor="curation", reason="drift")
    with pytest.raises(DivergenceError, match="no _sync.json marker"):
        lab.git.revert_to(target, actor="curation", reason="rollback")
    assert lab.memories.get(mid)["state"] == "CANDIDATE_MEMORY"
    lab.git.revert_to(target, actor="curation", reason="forced", force=True)
    assert lab.memories.get(mid)["state"] == "OBSERVED"


def test_empty_db_rebuild_works(lab):
    mid = lab.promote("rebuildable lesson")
    lab.git.commit_all("curation", "seed")
    with lab.db.transaction() as conn:
        for table in ("tier_c_memory_revisions", "tier_c_validations",
                      "tier_c_memories", "tier_c_block_revisions",
                      "tier_c_blocks", "tier_c_procedures"):
            conn.execute(f"DELETE FROM {table}")
    lab.git.restore()  # empty DB is the canonical rebuild precondition
    assert lab.memories.get(mid)["state"] == "CORE"


def test_manifest_column_whitelist(lab):
    """Audit L1: manifest keys are data, never SQL."""
    lab.observe("benign", source_ids=["src"])
    lab.git.commit_all("curation", "seed")
    malicious = [{"memory_id": "evil-1", "content": "x",
                  "type); DROP TABLE events; --": "evil"}]
    (lab.git.git_dir / "tier_c_memories.json").write_text(
        json.dumps(malicious), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown columns"):
        lab.git.restore()
    with lab.db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    assert n >= 0  # events table intact


def test_git_is_never_truth_for_tier_a(lab):
    """The git dir contains ONLY tier_c manifests; Tier A state must not be
    snapshot there."""
    lab.observe("lesson", source_ids=["src"])
    lab.git.commit_all("curation", "seed")
    tracked = lab.git._git("ls-files").stdout.split()
    assert all(name.startswith("tier_c_") or name in ("_sync.json",)
               for name in tracked), f"unexpected files in Tier C git: {tracked}"
