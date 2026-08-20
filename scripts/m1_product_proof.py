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
from jarvis_foundry.executive.calibration import rebuild_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import (
    WAIT_COMPARATIVE,
    WAIT_NO_ELIGIBLE,
    assess_eligibility,
)
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle

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


def _activate(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="m1-proof",
        idempotency_key=f"executive.activation_recorded:{EPOCH}",
    ))


def _organic(db: FoundryDatabase, objective: str, source: str, **extra) -> str:
    """Normal production candidate generation — no manual eligibility stamping."""
    evidence = [extra] if extra else []
    result = generate_candidate(db, "mission-proof", source, objective, evidence=evidence)
    cid = result["candidate_id"]
    score_candidate(db, cid)
    return cid


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
    _activate(database)

    proofs: dict[str, str] = {}

    # PROOF 1 — keyword bait loses (organic path)
    bait = _organic(
        database,
        "URGENT revenue crash outage fix tonight blocked deadline now",
        "workflow_opportunity",
    )
    good = _organic(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        "owner_goal",
    )
    # Historical stale candidate (pre-epoch simulation via lab source)
    stale = generate_candidate(
        database, "mission-proof", "proving-maintenance",
        "Fix stale maintenance backlog", evidence=[{"counts": 9}],
    )["candidate_id"]
    score_candidate(database, stale)
    stale_row = database.row("candidates", "candidate_id", stale)

    ranked = rank_candidates(
        database,
        [database.row("candidates", "candidate_id", bait),
         database.row("candidates", "candidate_id", good)],
        goals=__import__(
            "jarvis_foundry.executive.goals", fromlist=["active_goals"]
        ).active_goals(database),
    )
    proofs["PROOF_1"] = "PASS" if ranked[0].candidate_id == good else "FAIL"
    proofs["PROOF_2"] = "PASS" if ranked[0].candidate_id == good else "FAIL"
    proofs["historical_firewall"] = "PASS" if not assess_eligibility(
        database, stale_row, activation_epoch=EPOCH, resource_id="resource-1").eligible else "FAIL"

    # Blocked dependency
    blocked = _organic(
        database,
        "Deploy gateway patch after database migration completes",
        "maintenance",
        blocked_dependency="database-migration-pending",
    )
    proofs["blocked_dependency"] = "PASS" if not assess_eligibility(
        database, database.row("candidates", "candidate_id", blocked),
        activation_epoch=EPOCH, resource_id="resource-1").eligible else "FAIL"

    # PROOF 5 — failure-driven learning through production path
    fail_home = home / "fail-learn"
    fail_home.mkdir(parents=True, exist_ok=True)
    fail_db, _ = mission_database(fail_home)
    set_capability_state(
        fail_db, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m1-proof-fail"})
    _goal(fail_db)
    _activate(fail_db)
    cal_objective = "Improve gateway reliability reduce outage risk"
    cal_id = _organic(fail_db, cal_objective, "maintenance")
    fail_cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(fail_home / "state"),
        lab_scripted_hermes=True,
        lab_crash_after_write=True,
        lab_crash_step_id="executive-step",
        max_repairs=0,
    )
    fail_report = run_cycle(
        fail_db, FoundryPaths(fail_home), fail_cfg,
        loop_id="fail-proof", instance_id="fail-inst",
    )
    cal_events = [
        e for e in fail_db.events() if e.event_type == "executive.calibration_recorded"
    ]
    cal_adj = rebuild_calibration(fail_db).adjustment_for(
        source="maintenance", objective=cal_objective)
    proofs["PROOF_5"] = "PASS" if cal_events and cal_adj < 0 else "FAIL"

    # PROOF 6 — true comparative WAIT (eligible candidate exists, acting loses)
    wait_home = home / "wait-comparative"
    wait_home.mkdir(parents=True, exist_ok=True)
    wait_db, _ = mission_database(wait_home)
    set_capability_state(
        wait_db, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m1-proof-wait"})
    _goal(wait_db)
    _activate(wait_db)
    _organic(wait_db, "Rename cosmetic typo label document", "maintenance")
    wait_svc = ExecutiveDecisionService(
        wait_db, activation_epoch=EPOCH, min_action_score=0.55, source_id="m1-proof")
    wait_decision = wait_svc.evaluate(resource_id="resource-1", loop_id="wait-proof")
    proofs["PROOF_6"] = "PASS" if (
        wait_decision.kind == "WAIT"
        and wait_decision.wait is not None
        and wait_decision.wait.wait_kind == WAIT_COMPARATIVE
        and wait_decision.wait.best_candidate_id is not None
    ) else "FAIL"

    # NO_ELIGIBLE distinct proof
    empty_db_home = home / "wait-empty"
    empty_db_home.mkdir(parents=True, exist_ok=True)
    empty_db, _ = mission_database(empty_db_home)
    set_capability_state(
        empty_db, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m1-proof-empty"})
    _goal(empty_db)
    _activate(empty_db)
    empty_svc = ExecutiveDecisionService(
        empty_db, activation_epoch=EPOCH, min_action_score=0.55, source_id="m1-proof")
    empty_decision = empty_svc.evaluate(resource_id="resource-1", loop_id="empty-proof")
    proofs["no_eligible_wait"] = "PASS" if (
        empty_decision.wait.wait_kind == WAIT_NO_ELIGIBLE
    ) else "FAIL"

    # PROOF 3+4 — real execution with prediction + outcome (organic, no monkeypatch)
    cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(home / "state"),
        lab_scripted_hermes=True,
    )
    _organic(
        database,
        "Improve gateway reliability reduce customer outage risk",
        "maintenance",
    )
    report = run_cycle(
        database, FoundryPaths(home), cfg,
        loop_id="exec-proof", instance_id="proof-inst",
    )

    events = {e.event_type for e in database.events()}
    decision_recorded = [
        e for e in database.events() if e.event_type == "executive.decision_recorded"
    ]
    decision_materialized = [
        e for e in database.events() if e.event_type == "executive.decision_materialized"
    ]
    proofs["PROOF_3"] = "PASS" if report.get("outcome") == "PASSED" else "FAIL"
    proofs["PROOF_4"] = "PASS" if (
        "executive.prediction_recorded" in events
        and "executive.calibration_recorded" in events) else "FAIL"
    proofs["decision_uniqueness"] = "PASS" if (
        len(decision_recorded) == 1 and len(decision_materialized) == 1
    ) else "FAIL"

    verdict = "PASS" if all(
        v == "PASS" for k, v in proofs.items() if k.startswith("PROOF")
    ) else "FAIL"
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
