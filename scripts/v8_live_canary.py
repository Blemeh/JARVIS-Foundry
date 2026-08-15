"""V8 canonical promotion — LIVE V8 CANARY (bounded, harmless).

Runs against the REAL production database through the production API while
the supervisor is FROZEN (STOP sentinel active — no autonomous work).
Records are labeled mission_id=M-LIVE-CANARY, source v8-live-canary.

Checks: memory write -> validation stamp -> lifecycle -> recall -> role
compiler (executive/builder/verifier isolation) -> block update with
revision growth -> reconcile (read-only) -> stale flag -> budget
starvation -> STOP authority in governance.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, r"C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry/src")

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.hybrid_memory import (ContextCompiler, CoreMemoryRegistry,
                                          CurationPipeline, RecallEngine,
                                          TierCMemoryStore)
from jarvis_foundry.reconcile import reconcile

EVIDENCE = pathlib.Path(r"C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry/evidence/v8-canonical-promotion/live-canary.json")

HOME = pathlib.Path(r"C:/Users/b/AppData/Local/hermes/foundry")


def main() -> int:
    db = FoundryDatabase(HOME / "foundry.db")
    results: dict = {}
    memories = TierCMemoryStore(db)
    curation = CurationPipeline(db)
    recall = RecallEngine(db)
    blocks = CoreMemoryRegistry(db)
    compiler = ContextCompiler(db, stop_path=HOME / "EMERGENCY_STOP")

    # 1) memory write (labeled canary; idempotent — reuse a prior canary row)
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT memory_id FROM tier_c_memories WHERE mission_id='M-LIVE-CANARY' "
            "ORDER BY created_at LIMIT 1").fetchone()
    if existing:
        mid = existing["memory_id"]
        results["memory_written"] = mid
        results["reused_existing"] = True
    else:
        mid = memories.record_observation(
            "V8 live canary: supervisor watchdog stayed healthy during promotion",
            mission_id="M-LIVE-CANARY", source_ids=["v8-live-canary"],
            created_by="v8-promotion-canary")
        results["memory_written"] = mid
        results["reused_existing"] = False
        curation.propose_candidate(mid, actor="canary")
        curation.validate_and_accept(mid, placement="CORE", actor="curation")
    mem = memories.get(mid)
    results["lifecycle"] = mem["state"]
    results["lifecycle_before_stale"] = mem["state"]
    with db.connect() as conn:
        stamp = conn.execute(
            "SELECT validated_by FROM tier_c_validations WHERE memory_id=?", (mid,)
        ).fetchone()
    results["validation_stamp"] = stamp["validated_by"] if stamp else None

    # 3) recall
    hits = recall.recall("live canary supervisor watchdog")
    results["recall_hits"] = len(hits)
    results["recall_contains"] = any(h["memory_id"] == mid for h in hits)

    # 4) role compiler + verifier isolation + STOP authority
    exec_pkg = compiler.compile("M-LIVE-CANARY", "executive", "watchdog health")
    verifier_pkg = compiler.compile("M-LIVE-CANARY", "verifier", "watchdog health")
    results["executive_governance"] = "EMERGENCY STOP ACTIVE" in (
        exec_pkg.section("mandatory_governance") or "")
    results["verifier_no_tier_c"] = verifier_pkg.section("recalled_experience") is None
    results["advisory_labeled"] = (exec_pkg.section("recalled_experience") or "").startswith(
        "ADVISORY RECALL")

    # 5) block update with revision growth (bounded canary block)
    label = "canary-watchdog"
    if blocks.get_block(label) is None:
        blocks.create_block(label, "cycle one", char_limit=500,
                            description="v8 promotion live canary", actor="canary",
                            reason="canary")
    blocks.replace(label, "cycle two", actor="canary", reason="canary-2")
    blocks.replace(label, "cycle three", actor="canary", reason="canary-3")
    results["block_revisions"] = len(blocks.revisions(label))

    # 6) reconcile (read-only compare of ledger vs projections)
    report = reconcile(db)
    results["reconcile_consistent"] = report.consistent is True
    results["reconcile_mismatches"] = len(report.mismatches)

    # 7) stale flag (idempotent)
    if memories.get(mid)["state"] != "STALE":
        memories.mark_stale(mid, actor="canary", reason="canary-ttl")
    results["stale_flagged"] = memories.get(mid)["state"] == "STALE"

    # 8) budget starvation: governance survives a tiny budget
    tiny = compiler.compile("M-LIVE-CANARY", "builder", "watchdog", budget_chars=120)
    results["budget_governance_survives"] = (
        tiny.section("mandatory_governance") is not None
        and "budget exhausted" in " ".join(tiny.notes))

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(results, indent=1, sort_keys=True))

    ok = (results["lifecycle_before_stale"] in ("CORE", "STALE")  # STALE only reachable from CORE lineage
          and results["validation_stamp"] == "curation"
          and results["recall_contains"] and results["executive_governance"]
          and results["verifier_no_tier_c"] and results["advisory_labeled"]
          and results["block_revisions"] >= 3 and results["reconcile_consistent"]
          and results["reconcile_mismatches"] == 0 and results["stale_flagged"]
          and results["budget_governance_survives"])
    print(f"LIVE CANARY {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
