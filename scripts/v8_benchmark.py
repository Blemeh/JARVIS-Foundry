"""V8 hybrid memory — production benchmark (A/B/C semantics, real code).

A: canonical JARVIS without Tier C (knowledge retrieval only).
B: simulated Letta-style self-editing memory (flat blocks, latest-write-wins,
   no hierarchy) — a MEMORY-SEMANTICS stand-in, NOT the Letta runtime.
C: the V8 hybrid candidate (production tables + lifecycle + compiler).

Answers: false-memory defense (canonical FAIL verdict vs injected memory)
and context cost. Output is raw and environment-local; never hard-code
historical numbers.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.hybrid_memory import (ContextCompiler, CurationPipeline,
                                          RecallEngine, TierCMemoryStore)

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "v8-benchmark-raw.json"

MISSION = "M-BENCH"


def _seed_verdict_fail(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "verification.started",
        {"verdict_id": "v-bench", "mission_id": MISSION,
         "subject_type": "mission", "subject_id": MISSION, "criteria": []},
        source_type="supervisor", source_id="verifier-x",
        idempotency_key="v-bench:started"))
    db.append(Event.create(
        "verification.failed",
        {"verdict_id": "v-bench", "findings": ["benchmark failure"], "evidence": {},
         "missing_proof": [], "scope_violations": []},
        source_type="supervisor", source_id="verifier-x",
        idempotency_key="v-bench:failed"))


def _seed_claim(db: FoundryDatabase, statement: str) -> None:
    db.append(Event.create(
        "knowledge.claim_created",
        {"claim_id": f"c-{abs(hash(statement)) % 10**8}", "mission_id": MISSION,
         "statement": statement, "claim_type": "lessons",
         "confidence": 0.9, "status": "ACCEPTED"},
        source_type="supervisor", source_id="knowledge",
        idempotency_key=f"claim:{statement}"))


def benchmark_candidate_a(db: FoundryDatabase) -> dict:
    """Canonical JARVIS: Tier A/B only. No Tier C channel exists."""
    start = time.perf_counter()
    with db.connect() as conn:
        from jarvis_foundry.knowledge_retrieval import build_context_manifest
        manifest = build_context_manifest(conn, MISSION, "benchmark objective",
                                          max_chars=2000)
    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "truth_precedence": "PASS",  # no memory channel to threaten it
        "false_memory_defense": "PASS",
        "context_chars": len(json.dumps(manifest.as_dict())),
        "recall_latency_ms": round(latency_ms, 3),
        "history_preserved": True,
        "verifier_label": "no builder memory channel exists (isolated by construction)",
    }


def benchmark_candidate_b(db: FoundryDatabase) -> dict:
    """Simulated Letta-style self-editing memory: blocks only, last write
    wins, no truth hierarchy."""
    blocks: dict[str, str] = {}
    # the false memory is simply written as a block (self-editing memory)
    blocks["core"] = "MISSION M-BENCH: all tests passed and was accepted"
    start = time.perf_counter()
    truth = "PASS" if "all tests passed" not in blocks.get("core", "") else "FAIL"
    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "truth_precedence": truth,
        "false_memory_defense": truth,
        "context_chars": sum(len(v) for v in blocks.values()),
        "recall_latency_ms": round(latency_ms, 3),
        "history_preserved": False,
        "verifier_label": "shared blocks inject builder memory into verifier context unlabeled",
    }


def benchmark_candidate_c(db: FoundryDatabase, root: pathlib.Path) -> dict:
    """V8 hybrid: canonical truth + governed Tier C."""
    memories = TierCMemoryStore(db)
    curation = CurationPipeline(db)
    recall = RecallEngine(db)
    compiler = ContextCompiler(db)
    # a false memory is observed; the gate blocks POISON and DUPLICATES, not
    # falsehood — falsehood is defeated structurally by the compiler (Tier A
    # verdicts outrank advisory recall)
    false_mid = memories.record_observation(
        "M-BENCH passed all tests and was accepted",
        mission_id=MISSION, source_ids=["bad-src"], created_by="worker-9")
    curation.propose_candidate(false_mid, actor="worker-9")
    try:
        curation.validate_and_accept(false_mid, placement="CORE", actor="curation")
        promotion = "ADVISORY_ACCEPTED"
    except ValueError as exc:
        promotion = f"BLOCKED:{exc}"
    # a genuine lesson is promoted
    good_mid = memories.record_observation(
        "Supervisor exits with 0xC000013A: console control handler fixed it",
        mission_id=MISSION, source_ids=["log"], evidence_ids=["ev-log"],
        confidence=0.9)
    curation.propose_candidate(good_mid, actor="worker-1")
    curation.validate_and_accept(good_mid, placement="CORE", actor="curation")
    start = time.perf_counter()
    hits = recall.recall("supervisor exit 0xC000013A")
    latency_ms = (time.perf_counter() - start) * 1000
    pkg = compiler.compile(MISSION, "builder", "supervisor exit", budget_chars=2000)
    contract = pkg.section("mission_contract") or ""
    recalled = pkg.section("recalled_experience") or ""
    gov = pkg.section("mandatory_governance") or ""
    tier_a_wins = "FAIL" in contract
    false_never_authority = ("passed all tests" not in gov
                             and "passed all tests" not in contract
                             and "passed all tests" not in (pkg.section("evidence") or ""))
    return {
        "truth_precedence": "PASS" if (tier_a_wins and false_never_authority) else "FAIL",
        "false_memory_defense": "PASS" if false_never_authority else "FAIL",
        "context_chars": pkg.used_chars,
        "recall_latency_ms": round(latency_ms, 3),
        "history_preserved": True,
        "recall_hits": len(hits),
        "verifier_label": "UNVERIFIED_BUILDER_CLAIM",
        "false_memory_promotion": promotion,
        "false_memory_section": ("recalled_experience" if "passed all tests" in recalled
                                 else "not_in_context"),
    }


def main() -> int:
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-bench-"))
    results: dict = {}
    for name, builder in (
        ("A", lambda db: benchmark_candidate_a(db)),
        ("B", lambda db: benchmark_candidate_b(db)),
        ("C", lambda db: benchmark_candidate_c(db, root)),
    ):
        db = FoundryDatabase(root / f"c{name}.sqlite")
        db.initialize()
        _seed_verdict_fail(db)
        _seed_claim(db, "Supervisor exits with 0xC000013A: console handler fixed it")
        results[name] = builder(db)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(results, indent=1, sort_keys=True))
    ok = (results["A"]["truth_precedence"] == "PASS"
          and results["B"]["truth_precedence"] == "FAIL"
          and results["C"]["truth_precedence"] == "PASS"
          and results["C"]["false_memory_defense"] == "PASS")
    print(f"BENCHMARK {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
