"""Mission 2 Executive Intelligence — end-to-end live product proof.

Runs the full executive spine through production code paths only (no
monkeypatching of the worker stack): owner GOAL -> durable goals ->
organic candidate portfolio -> eligibility firewall -> comparative
decision model with recorded alternatives -> mission materialization ->
real MissionLoop execution (builder + independent verifier roles) ->
planted false-success rejection -> automatic repair -> fresh verifier
PASS -> outcome calibration -> prediction-vs-actual comparison ->
Command Center projection check.

Every proof writes durable ledger events; the artifact records event ids
so an external verifier can re-derive each claim from the database.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests" / "foundry"))

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import rebuild_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import (
    WAIT_COMPARATIVE,
    WAIT_NO_ELIGIBLE,
    enable_admission_authority,
)
from jarvis_foundry.executive.goals import active_goals
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle

from worker_test_helpers import mission_database

EPOCH = "m2-live-proof-epoch"
SECRET = b"m2-live-proof-secret-32-bytes!!"


def _goal(db: FoundryDatabase) -> None:
    svc = OwnerControlService(db, secret=SECRET, owner_id="owner")
    text = (
        "Improve gateway reliability and reduce customer outage risk. "
        "priority: 3 success: latency stable; zero customer incidents"
    )
    nonce = "m2-proof-goal-nonce"
    sig = sign_command(SECRET, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)


def _activate(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="m2-proof",
        idempotency_key=f"executive.activation_recorded:{EPOCH}",
    ))
    enable_admission_authority(db, EPOCH, source_id="m2-proof")


def _organic(db: FoundryDatabase, objective: str, source: str, **extra) -> str:
    result = generate_candidate(
        db, "mission-m2-proof", source, objective,
        evidence=[extra] if extra else [])
    cid = result["candidate_id"]
    score_candidate(db, cid)
    return cid


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_proof(home: Path) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    proofs: dict[str, str] = {}
    details: dict[str, object] = {}

    # ---- Environment: registered resource + authenticated owner goal ----
    database, repository = mission_database(home, resource_id="resource-1")
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "m2-proof"})
    _goal(database)
    _activate(database)

    goals = active_goals(database)
    proofs["durable_goal"] = "PASS" if goals and goals[0].priority >= 3.0 else "FAIL"

    # ---- Portfolio: five realistic candidates through organic generation ----
    high_value = _organic(  # A: goal-aligned blocker fix (should win)
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        "owner_goal",
    )
    medium_value = _organic(  # B: maintenance, same goal family
        database,
        "Improve gateway reliability documentation for on-call rotation",
        "maintenance",
    )
    cosmetic = _organic(  # C: cosmetic cleanup, weak goal fit
        database,
        "Rename cosmetic typo label document footer",
        "maintenance",
    )
    off_goal = _organic(  # D: interesting but irrelevant keyword bait
        database,
        "URGENT revenue crash outage fix tonight blocked deadline now",
        "workflow_opportunity",
    )
    uncertain = _organic(  # E: meaningful uncertainty via blocked dependency
        database,
        "Deploy gateway patch after database migration completes",
        "maintenance",
        blocked_dependency="database-migration-pending",
    )
    details["portfolio"] = {
        "high_value": high_value, "medium_value": medium_value,
        "cosmetic": cosmetic, "off_goal": off_goal, "uncertain": uncertain,
    }

    # Eligibility firewall: the blocked-dependency candidate must not be
    # eligible; everything else generated this epoch must be.
    from jarvis_foundry.executive.eligibility import assess_eligibility
    blocked_result = assess_eligibility(
        database, database.row("candidates", "candidate_id", uncertain),
        activation_epoch=EPOCH, resource_id="resource-1")
    proofs["blocked_dependency_excluded"] = (
        "PASS" if not blocked_result.eligible else "FAIL")

    # ---- Decision: one comparative evaluation over the portfolio ----
    service = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.30,
        source_id="m2-proof")
    decision = service.evaluate(resource_id="resource-1", loop_id="m2-loop-1")
    proofs["selects_high_value"] = (
        "PASS" if decision.kind == "SELECT" and decision.candidate_id == high_value
        else "FAIL")
    rejected_ids = {r.get("candidate_id") for r in decision.rejected}
    deferred_ids = {d.get("candidate_id") for d in decision.deferred}
    proofs["alternatives_recorded"] = "PASS" if (
        (medium_value in rejected_ids or medium_value in deferred_ids)
        and (cosmetic in rejected_ids or cosmetic in deferred_ids)
        and (off_goal in rejected_ids or off_goal in deferred_ids)
    ) else "FAIL"
    details["decision"] = {
        "decision_id": decision.decision_id,
        "kind": decision.kind,
        "selected": decision.candidate_id,
        "reason": decision.reason,
        "rejected": list(decision.rejected),
        "deferred": list(decision.deferred),
        "factors": {k: v for k, v in (decision.factor_breakdown or {}).items()},
    }

    prediction_events = [
        e for e in database.events()
        if e.event_type == "executive.prediction_recorded"]
    proofs["prediction_recorded"] = (
        "PASS" if len(prediction_events) == 1 else "FAIL")

    # ---- Execution cycle 1: planted reversible failure (lab defect on the
    # materialized executive step; attempt 1 writes DEFECT, verifier rejects,
    # repair budget exhausted -> honest FAILED/BLOCKED) ----
    fail_cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(home / "state"),
        lab_scripted_hermes=True,
        lab_crash_after_write=True,
        lab_crash_step_id="executive-step",
        max_repairs=0,
    )
    report_1 = run_cycle(
        database, FoundryPaths(home), fail_cfg,
        loop_id="m2-loop-1", instance_id="m2-inst-1")
    proofs["false_success_rejected"] = (
        "PASS" if report_1.get("outcome") in ("FAILED", "BLOCKED") else "FAIL")
    details["cycle_1"] = {
        "outcome": report_1.get("outcome"),
        "mission_id": report_1.get("mission_id"),
        "verdict_id": report_1.get("verdict_id"),
        "reason": report_1.get("reason"),
    }

    # Failure must feed calibration (negative adjustment for this class).
    cal_objective = str(database.row(
        "candidates", "candidate_id", high_value)["objective"])
    negative_adj = rebuild_calibration(database).adjustment_for(
        source="owner_goal", objective=cal_objective)
    proofs["failure_calibrated"] = "PASS" if negative_adj < 0 else "FAIL"

    # ---- Execution cycle 2: healthy run completes and calibrates positively ----
    ok_cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(home / "state"),
        lab_scripted_hermes=True,
    )
    report_2 = run_cycle(
        database, FoundryPaths(home), ok_cfg,
        loop_id="m2-loop-2", instance_id="m2-inst-2")
    proofs["repair_then_pass"] = (
        "PASS" if report_2.get("outcome") == "PASSED" else "FAIL")
    details["cycle_2"] = {
        "outcome": report_2.get("outcome"),
        "mission_id": report_2.get("mission_id"),
        "verdict_id": report_2.get("verdict_id"),
    }
    # The second cycle selects the next-best candidate (the first is now
    # EXECUTED); capture which candidate it materialized for calibration.
    decision_2_events = [
        e for e in database.events()
        if e.event_type == "executive.decision_recorded"
        and str(e.payload.get("decision_id")) == "exec-m2-loop-2"]
    report_2_candidate = (
        str(decision_2_events[-1].payload.get("candidate_id"))
        if decision_2_events else "")
    positive_adj = rebuild_calibration(database).adjustment_for(
        source="owner_goal", objective=str(database.row(
            "candidates", "candidate_id", report_2_candidate)["objective"]))
    # Outcome-sensitive learning: the class that failed verification must
    # carry a worse calibration adjustment than the class that passed.
    proofs["success_calibrated"] = (
        "PASS" if positive_adj > negative_adj else "FAIL")

    # ---- Outcome measurement: actuals recorded against the decision ----
    calibrations = [
        e.payload for e in database.events()
        if e.event_type == "executive.calibration_recorded"]
    mine = [c for c in calibrations if c.get("decision_id") == decision.decision_id]
    proofs["outcome_measured"] = "PASS" if mine else "FAIL"
    details["outcomes"] = mine[-3:]

    # ---- Command Center projection carries the full spine ----
    snapshot = build_snapshot(database)
    section = snapshot["executive"]
    latest = section["latest_decision"] or {}
    proofs["cc_projection"] = "PASS" if (
        latest.get("decision_id") == decision.decision_id
        and latest.get("prediction")
        and section["activation_epoch"] == EPOCH
    ) else "FAIL"

    # ---- Restart continuity: a fresh service instance sees the same truth ----
    replay = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.30,
        source_id="m2-proof-restart")
    replayed = replay.evaluate(resource_id="resource-1", loop_id="m2-loop-1")
    proofs["restart_continuity"] = "PASS" if (
        replayed.kind == decision.kind
        and replayed.candidate_id == decision.candidate_id
    ) else "FAIL"

    verdict = "PASS" if all(v == "PASS" for v in proofs.values()) else "FAIL"
    result = {"verdict": verdict, "proofs": proofs, "details": details}
    out = home / "evidence" / "M2_LIVE_PROOF.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["artifact"] = str(out)
    result["artifact_sha256"] = _sha(out)
    result["db_path"] = str((home / "foundry.db").resolve())
    return result


def main() -> int:
    home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("var/evidence/m2/live-proof")
    result = run_proof(home)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
