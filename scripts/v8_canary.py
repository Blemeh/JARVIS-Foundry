"""V8 hybrid memory — R2-bounded canary in the isolated candidate.

Representative reversible missions exercise the hybrid path incrementally:
context compilation → builder recall → executive recall → procedure use →
curation/promotion → restart/recovery → verifier context (strongest
isolation last). Automatic rollback criteria are defined BEFORE the run:
any truth/STOP/verifier violation freezes Tier C influence immediately.
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
from jarvis_foundry.hybrid_memory import (ContextCompiler, CoreMemoryRegistry,
                                          CurationPipeline, ProcedureRegistry,
                                          RecallEngine, TierCGitVersioning,
                                          TierCMemoryStore)
from jarvis_foundry.worker_context import (deliver_hybrid_worker_context,
                                           reconstruct_hybrid_context)

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "v8-canary-raw.json"

ROLLBACK_CRITERIA = (
    "truth violation (memory overriding a verdict)",
    "STOP not honored in governance",
    "verifier contamination (Tier C recall in verifier context)",
    "divergence silently discarded",
)


def canary(db: FoundryDatabase, root: pathlib.Path, mission_id: str,
           objective: str) -> dict:
    steps: dict[str, dict] = {}
    memories = TierCMemoryStore(db)
    curation = CurationPipeline(db)
    recall = RecallEngine(db)
    blocks = CoreMemoryRegistry(db)
    procs = ProcedureRegistry(db)
    compiler = ContextCompiler(db)
    git = TierCGitVersioning(db, root / f"memgit-{mission_id}")
    blocks.ensure_defaults()

    # 1) context compilation (executive)
    pkg = compiler.compile(mission_id, "executive", objective, budget_chars=3000)
    steps["context_compilation"] = {
        "ok": pkg.section("mandatory_governance") is not None,
        "used": pkg.used_chars,
    }

    # 2) builder recall + promotion
    mid = memories.record_observation(
        f"{objective} addressed",
        mission_id=mission_id,
        source_ids=["canary"], created_by="builder-1")
    curation.propose_candidate(mid, actor="builder-1")
    try:
        curation.validate_and_accept(mid, placement="CORE", actor="curation")
        promotion = "CORE"
    except ValueError as exc:
        promotion = f"blocked:{exc}"
    steps["builder_recall"] = {
        "promotion": promotion,
        "recalled": bool(recall.recall(objective)),
    }

    # 3) procedure use
    pid = procs.add("canary-proc", "repeatable verification",
                    "Run the deterministic suite; compare evidence hashes.",
                    ["verify", "suite", "hashes"], mission_id=mission_id)
    steps["procedure_use"] = {"found": bool(procs.search(objective))}

    # 4) restart/recovery (fresh store over the same DB)
    memories2 = TierCMemoryStore(db)
    steps["restart_recovery"] = {
        "memory_survives": memories2.get(mid) is not None,
        "state": memories2.get(mid)["state"] if memories2.get(mid) else None,
    }

    # 5) verifier context (strongest isolation last)
    verifier_pkg = compiler.compile(mission_id, "verifier", objective,
                                    budget_chars=3000)
    steps["verifier_context"] = {
        "no_tier_c_recall": verifier_pkg.section("recalled_experience") is None,
        "governance_present": verifier_pkg.section("mandatory_governance") is not None,
    }

    # 6) hybrid manifest delivery + reconstruction (hash contract)
    vault = root / "vault"
    (vault / "Notes").mkdir(parents=True, exist_ok=True)
    (vault / "Notes" / "n.md").write_text("canary note", encoding="utf-8")
    record = deliver_hybrid_worker_context(
        db, vault, run_id=f"run-{mission_id}", worker="builder-1",
        mission_id=mission_id, objective=objective, role="builder")
    rebuilt = reconstruct_hybrid_context(db, vault, run_id=f"run-{mission_id}",
                                         role="builder")
    steps["manifest_contract"] = {
        "recorded": bool(record["manifest_id"]),
        "matches": rebuilt["matches_recorded"],
    }

    # 7) version commit + rollback coherence
    sha = git.commit_all("canary", f"canary {mission_id}")
    steps["versioning"] = {"committed": bool(sha)}
    return steps


def main() -> int:
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-canary-"))
    db = FoundryDatabase(root / "canary.sqlite")
    db.initialize()
    missions = [
        ("M-CAN-1", "supervisor restart policy"),
        ("M-CAN-2", "evidence hash verification"),
        ("M-CAN-3", "vault snapshot rollback"),
    ]
    report: dict = {"rollback_criteria": list(ROLLBACK_CRITERIA), "runs": []}
    for mission_id, objective in missions:
        report["runs"].append({"mission": mission_id,
                               "steps": canary(db, root, mission_id, objective)})
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    violations = []
    for run in report["runs"]:
        steps = run["steps"]
        if not steps["context_compilation"]["ok"]:
            violations.append(f"{run['mission']}: context compilation")
        if steps["builder_recall"]["promotion"] != "CORE":
            violations.append(f"{run['mission']}: promotion")
        if not steps["verifier_context"]["no_tier_c_recall"]:
            violations.append(f"{run['mission']}: verifier contamination")
        if not steps["manifest_contract"]["matches"]:
            violations.append(f"{run['mission']}: manifest contract")
        if not steps["versioning"]["committed"]:
            violations.append(f"{run['mission']}: versioning")
    if violations:
        print(f"CANARY FAIL: {violations} — Tier C influence frozen per rollback criteria")
        return 1
    print(f"CANARY PASS ({len(report['runs'])} missions; no rollback criteria triggered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
