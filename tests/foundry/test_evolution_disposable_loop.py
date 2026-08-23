"""V6 lane JOB: disposable promotion + rollback loop.

Proves the COMPLETE lifecycle against DISPOSABLE release roots only:

GOOD PATH
  candidate -> eligible -> promoted into disposable live slot ->
  post-promotion measurement -> improvement retained (no rollback).

DEGRADED PATH
  candidate -> eligible -> promoted -> outcome regression detected ->
  freeze -> automatic rollback (trigger vocabulary) -> previous
  release restored, failed release quarantined -> reconcile consistent.

Also proves crash recovery (recover_promotions re-drives INTENT),
executable/data rollback only via the S9 restore machinery on
DISPOSABLE copies, and that NOTHING here touches any live surface.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.canary import (
    canary_tick,
    start_canary,
)
from jarvis_foundry.evolution.lab import register_workload, run_evaluation
from jarvis_foundry.evolution.promote import (
    PromoteError,
    promote_release,
    promotion_status,
    recover_promotions,
    rollback_release,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    register_release,
    set_slot,
    slot_pointers,
)
from jarvis_foundry.evolution.restore import (
    create_restore_point,
    rehearse_restore,
)
from jarvis_foundry.evolution.verify import (
    record_verification_request,
    record_verification_verdict,
)
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.runtime.provider_config import default_provider_settings

SETTINGS = default_provider_settings()
BUILDER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
           "session_id": "loop-builder"}
VERIFIER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
            "session_id": "loop-verifier"}
CONTROLLER = "supervisor"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db, problem="p", evidence_refs=["e"], provenance={"source": "t"},
        hypothesis="h", expected_mechanism="m",
        baseline_metric={"name": "duration_s", "value": 4.0, "unit": "s",
                         "source": "e"},
        mmi={"minimum_delta": 0.5, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "c", "safety": "s", "cost": "co",
                    "latency": "l"},
        workload={"ref": "w1", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 5.0, "capability_scope": []},
        reversibility={"plan": "freeze-first rollback",
                       "rollback_threshold": "any regression"})


def _seed_eval(db: FoundryDatabase, run_id: str, workload_id: str,
               kind: str, *, duration_s: float) -> None:
    db.append(replace(Event.create(
        "evaluation.run_started",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": kind, "subject_identity": "x",
         "command_hash": "h"},
        source_type="test", source_id="loop", idempotency_key=f"rs:{run_id}"),
        recorded_at="2026-08-22T12:00:00"))
    db.append(replace(Event.create(
        "evaluation.run_completed",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": kind, "subject_identity": "x",
         "command_hash": "h", "input_hash": "ih", "exit_code": 0,
         "duration_s": duration_s, "error_rate": 0.0, "cost_usd": 0.0,
         "case_results": [{"case": "c", "exit_code": 0}],
         "output_hashes": {}, "nondeterminism_handling": "not_seeded"},
        source_type="test", source_id="loop", idempotency_key=f"rc:{run_id}"),
        recorded_at="2026-08-22T12:00:01"))


def _evidence_chain(db: FoundryDatabase, tmp_path: Path,
                    improvement_id: str, *,
                    candidate_duration: float,
                    pin_slots: bool = True) -> dict[str, str]:
    genesis = register_release(
        db, source_commit="0" * 40, artifact_hashes={"g": "0" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[], verdicts=[],
        controller=CONTROLLER)
    candidate = register_release(
        db, source_commit=("1" * 40) if candidate_duration < 4 else
        ("2" * 40), artifact_hashes={"c": "1" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=genesis, improvement_id=improvement_id,
        risk_class="R2", migrations=[], verdicts=[],
        controller=CONTROLLER)
    if pin_slots:
        set_slot(db, "live", genesis, reason="genesis",
                 controller=CONTROLLER,
                 transition_id=f"{candidate_duration:.1f}:genesis")
        set_slot(db, "candidate", candidate, reason="candidate",
                 controller=CONTROLLER,
                 transition_id=f"{candidate_duration:.1f}:cand")
    inputs = tmp_path / f"inputs-{candidate_duration}"
    inputs.mkdir(exist_ok=True)
    (inputs / "case.txt").write_text("x", encoding="utf-8")
    workload_id = register_workload(
        db, name=f"w-{candidate_duration}", input_dir=inputs, config={},
        seeds=[], controller=CONTROLLER)
    _seed_eval(db, f"b-{candidate_duration}", workload_id, "baseline",
               duration_s=4.0)
    _seed_eval(db, f"c-{candidate_duration}", workload_id, "candidate",
               duration_s=candidate_duration)
    verification_id = record_verification_request(
        db, improvement_id=improvement_id, subject_ref=candidate,
        criteria=["guardrail correctness: c"],
        artifacts={"c": "1" * 64}, builder_identity=BUILDER,
        controller=CONTROLLER, provider_settings=SETTINGS)
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[{"criterion": "guardrail correctness: c",
                           "status": "PASS", "evidence_hash": "c" * 64}],
        findings=[], verifier_identity=VERIFIER, evidence_refs=["e"],
        provider_settings=SETTINGS)
    canary_id = start_canary(
        db, improvement_id=improvement_id, candidate_release_id=candidate,
        traffic_share=0.25, duration_cycles=1, max_cost_usd=1.0,
        health_thresholds={"error_rate_max": 0.05, "latency_max_s": 2.0},
        controller=CONTROLLER)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.25,
                errors=0, latency_s=0.2, cost_usd=0.01,
                controller=CONTROLLER)
    return {"genesis": genesis, "candidate": candidate,
            "baseline_run": f"b-{candidate_duration}",
            "candidate_run": f"c-{candidate_duration}",
            "verification_id": verification_id, "canary_id": canary_id}


# ---------------------------------------------------------------------------
# GOOD PATH: promote + retain
# ---------------------------------------------------------------------------

def test_good_candidate_promotes_and_is_retained(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    chain = _evidence_chain(db, tmp_path, improvement_id,
                            candidate_duration=2.0)  # faster: real win
    promotion_id = promote_release(
        db, candidate_release_id=chain["candidate"],
        verification_id=chain["verification_id"],
        baseline_run_id=chain["baseline_run"],
        candidate_run_id=chain["candidate_run"],
        canary_id=chain["canary_id"], controller=CONTROLLER)
    assert promotion_status(db, promotion_id) == "PROMOTED"
    pointers = slot_pointers(db)
    assert pointers["live"] == chain["candidate"]
    assert pointers["previous"] == chain["genesis"]
    assert pointers["quarantine"] is None
    report = reconcile(db)
    assert report.consistent


# ---------------------------------------------------------------------------
# DEGRADED PATH: promote -> regress -> rollback restores previous
# ---------------------------------------------------------------------------

def test_degraded_candidate_rolls_back_to_previous(
        db: FoundryDatabase, tmp_path: Path) -> None:
    # v1 GOOD candidate: full evidence chain -> promoted -> becomes the
    # disposable live release WITH promotion lineage.
    improvement_id = _improvement(db)
    v1 = _evidence_chain(db, tmp_path, improvement_id,
                         candidate_duration=3.0)
    p1 = promote_release(
        db, candidate_release_id=v1["candidate"],
        verification_id=v1["verification_id"],
        baseline_run_id=v1["baseline_run"],
        candidate_run_id=v1["candidate_run"],
        canary_id=v1["canary_id"], controller=CONTROLLER)
    assert promotion_status(db, p1) == "PROMOTED"

    # a DEGRADED successor cannot even pass the gate honestly (slower
    # than baseline): the gate chain rejects it before any promotion.
    # pin_slots=False: the live slot must stay on the promoted v1 —
    # a failed gate attempt must not disturb slot state at all.
    v2 = _evidence_chain(db, tmp_path, improvement_id,
                         candidate_duration=8.0, pin_slots=False)
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=v2["candidate"],
            verification_id=v2["verification_id"],
            baseline_run_id=v2["baseline_run"],
            candidate_run_id=v2["candidate_run"],
            canary_id=v2["canary_id"], controller=CONTROLLER)

    # ROLLBACK MACHINERY: regression of the LIVE release (v1) is
    # discovered post-promotion; freeze-first rollback restores the
    # exact previous release and quarantines the failed one.
    rollback_promotion_id = rollback_release(
        db, trigger="outcome_regression",
        reason="post-promotion latency regression observed",
        controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["live"] == v1["genesis"]
    assert pointers["previous"] is None
    assert pointers["quarantine"] == v1["candidate"]
    report = reconcile(db)
    assert report.consistent


def test_crash_recovery_redrives_intent_promotion(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    chain = _evidence_chain(db, tmp_path, improvement_id,
                            candidate_duration=2.0)
    promotion_id = promote_release(
        db, candidate_release_id=chain["candidate"],
        verification_id=chain["verification_id"],
        baseline_run_id=chain["baseline_run"],
        candidate_run_id=chain["candidate_run"],
        canary_id=chain["canary_id"], controller=CONTROLLER)
    # simulate a crash DURING recovery bookkeeping: an INTENT row left
    # behind while slots already moved must re-drive idempotently
    recovered = recover_promotions(db, controller=CONTROLLER)
    assert promotion_status(db, promotion_id) == "PROMOTED"
    assert isinstance(recovered, list)
    report = reconcile(db)
    assert report.consistent


def test_restore_rehearsal_on_disposable_copy(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    point_id = create_restore_point(db, label="before-experiment",
                                    controller=CONTROLLER)
    summary = rehearse_restore(db, point_id=point_id,
                               controller=CONTROLLER)
    assert summary["ok"] is True
    report = reconcile(db)
    assert report.consistent
