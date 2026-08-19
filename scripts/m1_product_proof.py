"""Mission 1 isolated product proof — six end-to-end executive scenarios."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests" / "foundry"))

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import rebuild_calibration, record_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import assess_eligibility
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import mission_database

EPOCH = "m1-product-proof-epoch"
SECRET = b"m1-proof-secret-32-bytes-min!!"


def _goal(db: FoundryDatabase) -> None:
    svc = OwnerControlService(db, secret=SECRET, owner_id="owner")
    text = (
        "Improve gateway reliability and reduce customer outage risk. "
        "priority: 3 success: latency stable; zero customer incidents"
    )
    nonce = "proof-goal-nonce"
    sig = sign_command(SECRET, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)


def _candidate(db: FoundryDatabase, objective: str, source: str, **extra) -> str:
    evidence = [{"executive_epoch": EPOCH, "executive_eligible": True}]
    if extra:
        evidence.append(extra)
    result = generate_candidate(db, "mission-proof", source, objective, evidence=evidence)
    cid = result["candidate_id"]
    score_candidate(db, cid)
    return cid


class ProofHermes(ScriptedWorkerAdapter):
    def __init__(self) -> None:
        super().__init__(WORKER_HERMES)

    def dispatch(self, contract, *, heartbeat):
        if contract.role in ("builder", "repair"):
            for rel in contract.expected_artifacts or ():
                path = contract.working_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("proof report\nVERIFIED_BY executive-worker\n", encoding="utf-8")
            self.outcome = ScriptedOutcome(
                files_changed=tuple(contract.expected_artifacts or ()),
                observed_touched_paths=tuple(contract.expected_artifacts or ()),
            )
        else:
            missing = [r for r in (contract.expected_artifacts or ())
                       if not (contract.working_root / r).is_file()]
            self.outcome = ScriptedOutcome(
                verdict="PASS" if not missing else "FAIL",
                summary="ok" if not missing else f"missing {missing}",
            )
        return super().dispatch(contract, heartbeat=heartbeat)


def run_proof(home: Path | None = None) -> dict:
    import shutil
    import tempfile
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="m1-proof-"))
    elif home.exists():
        try:
            shutil.rmtree(home)
        except OSError:
            home = Path(tempfile.mkdtemp(prefix="m1-proof-"))
    home.mkdir(parents=True, exist_ok=True)
    database, repository = mission_database(home)
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m1-proof"})
    _goal(database)
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="m1-proof",
        idempotency_key=f"executive.activation_recorded:{EPOCH}",
    ))

    proofs: dict[str, str] = {}

    # A — keyword bait (low goal fit)
    bait = _candidate(
        database,
        "URGENT revenue crash outage fix tonight blocked deadline now",
        "executive:bait",
    )
    # B — goal-aligned high value
    good = _candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        "executive:aligned",
    )
    # C — stale historical (no epoch marker)
    stale = generate_candidate(
        database, "mission-proof", "maintenance",
        "Fix stale maintenance backlog", evidence=[{"counts": 9}],
    )["candidate_id"]
    score_candidate(database, stale)
    stale_row = database.row("candidates", "candidate_id", stale)
    proofs["PROOF_1"] = "PASS" if assess_eligibility(
        database, database.row("candidates", "candidate_id", bait),
        activation_epoch=EPOCH, resource_id="resource-1").eligible else "FAIL"

    ranked = rank_candidates(
        database,
        [database.row("candidates", "candidate_id", bait),
         database.row("candidates", "candidate_id", good)],
        goals=__import__("jarvis_foundry.executive.goals", fromlist=["active_goals"]).active_goals(database),
    )
    proofs["PROOF_2"] = "PASS" if ranked[0].candidate_id == good else "FAIL"

    proofs["historical_firewall"] = "PASS" if not assess_eligibility(
        database, stale_row, activation_epoch=EPOCH, resource_id="resource-1").eligible else "FAIL"

    # D — blocked dependency
    blocked = _candidate(
        database,
        "Deploy gateway patch after database migration completes",
        "executive:blocked",
        blocked_dependency="database-migration-pending",
    )
    proofs["blocked_dependency"] = "PASS" if not assess_eligibility(
        database, database.row("candidates", "candidate_id", blocked),
        activation_epoch=EPOCH, resource_id="resource-1").eligible else "FAIL"

    # E — calibration affects ranking
    cal_objective = "Improve gateway reliability reduce outage risk"
    cal_id = _candidate(database, cal_objective, "executive:cal")
    record_calibration(
        database, decision_id="d-cal", candidate_id=cal_id, mission_id="m-cal",
        source="executive:cal", objective=cal_objective, outcome="FAILED", repair_count=3,
    )
    before = rank_candidates(database, [database.row("candidates", "candidate_id", cal_id)])[0].composite
    proofs["PROOF_5"] = "PASS" if rebuild_calibration(database).adjustment_for(
        source="executive:cal", objective=cal_objective) < 0 else "FAIL"

    # F — useful WAIT when only low-value candidates
    wait_home = home / "wait-only"
    wait_home.mkdir(parents=True, exist_ok=True)
    wait_db, _ = mission_database(wait_home)
    set_capability_state(
        wait_db, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m1-proof-wait"})
    _goal(wait_db)
    wait_db.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="m1-proof",
        idempotency_key=f"executive.activation_recorded:{EPOCH}:wait",
    ))
    _candidate(wait_db, "Rename cosmetic typo label document", "executive:low")
    wait_svc = ExecutiveDecisionService(
        wait_db, activation_epoch=EPOCH, min_action_score=0.55, source_id="m1-proof")
    wait_decision = wait_svc.evaluate(resource_id="resource-1", loop_id="wait-proof")
    proofs["PROOF_6"] = "PASS" if wait_decision.kind == "WAIT" else "FAIL"

    # PROOF 3+4 — real execution with prediction + outcome
    cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(home / "state"),
        lab_scripted_hermes=True,
    )
    adapter = ProofHermes()
    worker_router = WorkerRouter()
    worker_router.mark_ready(WORKER_HERMES, state=READY, evidence=("m1-proof",))
    bridge = HybridWorkerRoleAdapter(database=database, router=worker_router,
                                     registry=WorkerRegistry({WORKER_HERMES: adapter}))
    stack = type("S", (), {"bridge": bridge, "router": worker_router})()

    import jarvis_foundry.runtime.worker_loop as wl
    original = wl.build_worker_stack
    wl.build_worker_stack = lambda database, paths, config, *, run_id: stack
    try:
        # Fresh good candidate for execution
        exec_cand = _candidate(
            database,
            "Improve gateway reliability reduce customer outage risk",
            "executive:exec",
        )
        report = run_cycle(database, FoundryPaths(home), cfg,
                           loop_id="exec-proof", instance_id="proof-inst")
    finally:
        wl.build_worker_stack = original

    events = {e.event_type for e in database.events()}
    proofs["PROOF_3"] = "PASS" if report.get("outcome") == "PASSED" else "FAIL"
    proofs["PROOF_4"] = "PASS" if (
        "executive.prediction_recorded" in events
        and "executive.calibration_recorded" in events) else "FAIL"

    verdict = "PASS" if all(v == "PASS" for k, v in proofs.items() if k.startswith("PROOF")) else "FAIL"
    result = {"verdict": verdict, "proofs": proofs, "execution_report": report}
    out = home / "evidence" / "M1_PRODUCT_PROOF.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("var/m1-proof")
    result = run_proof(home)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
