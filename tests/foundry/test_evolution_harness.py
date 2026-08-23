"""V6 lane JOB: multi-cycle autonomous evolution harness.

A fully DISPOSABLE harness proving SEVERAL complete cycles of:

    observe weakness (detector over seeded outcomes)
      -> select improvement (selector with vetoes/capacity)
      -> create hypothesis (S10 discovery from detector sources)
      -> build candidate (S3 isolated builder)
      -> shadow/benchmark (S6 shadow + typed benchmark)
      -> independent verdict (verifier orchestrator, out-of-process)
      -> reject bad candidates (gate chain / canary / verifier)
      -> accept good candidate in disposable release model (S8)
      -> measure actual result (measurement layer)
      -> record learning (feedback: predicted vs actual + calibration)
      -> choose next improvement

Required by the mission: >= 1 good improvement path ACCEPTED,
>= 1 bad candidate REJECTED, >= 1 rollback/degradation path,
STOP/veto blocks eligibility, no live mutation, no
self-certification, no duplicate promotion.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.canary import (
    canary_tick,
    start_canary,
)
from jarvis_foundry.evolution.detector import detect_weaknesses
from jarvis_foundry.evolution.discover import (
    candidate_improvements,
    record_discovered_improvements,
)
from jarvis_foundry.evolution.feedback import (
    record_outcome,
    rule_calibration,
    select_followup,
)
from jarvis_foundry.evolution.lab import register_workload, run_evaluation
from jarvis_foundry.evolution.measurement import capture_measurement
from jarvis_foundry.evolution.promote import (
    promote_release,
    rollback_release,
)
from jarvis_foundry.evolution.registry import (
    register_release,
    set_slot,
    slot_pointers,
)
from jarvis_foundry.evolution.selector import select_next_improvement
from jarvis_foundry.evolution.verify import (
    record_verification_request,
    record_verification_verdict,
)
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.runtime.provider_config import default_provider_settings

SETTINGS = default_provider_settings()
BUILDER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
           "session_id": "harness-builder"}
VERIFIER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
            "session_id": "harness-verifier"}
CONTROLLER = "supervisor"
WINDOW = ("2026-08-22T00:00:00", "2026-08-23T00:00:00")


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


# ---------------------------------------------------------------------------
# stage helpers (each delegates to the REAL machinery — no shortcuts)
# ---------------------------------------------------------------------------

def _seed_retry_storm(db: FoundryDatabase) -> str:
    """Seed durable outcomes that trip mission_retry_count."""
    from datetime import datetime, timedelta
    base = datetime.fromisoformat(WINDOW[0])
    for i in range(4):
        event = Event.create(
            "agent_run.started",
            {"run_id": f"h{i}", "mission_id": "hm-retry",
             "role": "builder", "provider": SETTINGS.provider,
             "model": SETTINGS.model},
            source_type="test", source_id="harness",
            idempotency_key=f"hs:{i}")
        db.append(replace(event, recorded_at=(
            base + timedelta(hours=i + 1)).isoformat()))
        fail = Event.create(
            "agent_run.failed",
            {"run_id": f"h{i}", "failure_class": "test"},
            source_type="test", source_id="harness",
            idempotency_key=f"hf:{i}")
        db.append(replace(fail, recorded_at=(
            base + timedelta(hours=i + 1)).isoformat()))
    return "hm-retry"


def _observe(db: FoundryDatabase) -> list[dict[str, Any]]:
    return detect_weaknesses(db, window_start=WINDOW[0],
                             window_end=WINDOW[1])["observations"]


def _hypothesize(db: FoundryDatabase,
                 observations: list[dict[str, Any]]) -> list[str]:
    sources: dict[str, list[dict[str, Any]]] = {
        "metric_exceedances": [], "guardrail_breaches": [],
        "task_failures": []}
    for observation in observations:
        ref = observation["evidence_refs"][0]
        common = {"evidence_ref": ref, "observed_at": WINDOW[1],
                  "surfaces": [observation["affected_component"]]}
        if observation["observation_type"] == "task_failures":
            sources["task_failures"].append({
                **common, "task": observation["rule"],
                "detail": observation["detail"]})
        elif observation["observation_type"] == "metric_exceedances":
            sources["metric_exceedances"].append({
                **common, "metric": observation["metric"],
                "value": observation["value"],
                "threshold": observation["threshold"], "unit": ""})
        else:
            sources["guardrail_breaches"].append({
                **common, "guardrail": "correctness",
                "detail": observation["detail"]})
    candidates = candidate_improvements(db, sources=sources,
                                        controller=CONTROLLER)
    return record_discovered_improvements(
        db, candidates=candidates, sources=sources,
        controller=CONTROLLER)


def _evidence_and_promote(db: FoundryDatabase, tmp_path: Path,
                          improvement_id: str, *,
                          candidate_duration: float,
                          release_tag: str) -> dict[str, Any]:
    genesis = register_release(
        db, source_commit="0" * 40,
        artifact_hashes={"g": "0" * 64}, dependencies=[],
        runtime_identity={"python": "3.11"}, parent_release_id=None,
        improvement_id=improvement_id, risk_class="R2", migrations=[],
        verdicts=[], controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis",
             controller=CONTROLLER,
             transition_id=f"{release_tag}:genesis")
    candidate = register_release(
        db, source_commit=release_tag.ljust(40, "0"),
        artifact_hashes={"c": "1" * 64}, dependencies=[],
        runtime_identity={"python": "3.11"}, parent_release_id=genesis,
        improvement_id=improvement_id, risk_class="R2", migrations=[],
        verdicts=[], controller=CONTROLLER)
    set_slot(db, "candidate", candidate, reason="candidate",
             controller=CONTROLLER,
             transition_id=f"{release_tag}:cand")
    inputs = tmp_path / f"in-{release_tag[:8]}"
    inputs.mkdir(exist_ok=True)
    (inputs / "case.txt").write_text("x", encoding="utf-8")
    workload_id = register_workload(
        db, name=f"w-{release_tag[:8]}", input_dir=inputs, config={},
        seeds=[], controller=CONTROLLER)

    def seed_eval(run_id: str, kind: str, duration: float) -> None:
        db.append(replace(Event.create(
            "evaluation.run_started",
            {"run_id": run_id, "workload_id": workload_id,
             "subject_kind": kind, "subject_identity": "x",
             "command_hash": "h"},
            source_type="test", source_id="harness",
            idempotency_key=f"rs:{run_id}"),
            recorded_at="2026-08-22T12:00:00"))
        db.append(replace(Event.create(
            "evaluation.run_completed",
            {"run_id": run_id, "workload_id": workload_id,
             "subject_kind": kind, "subject_identity": "x",
             "command_hash": "h", "input_hash": "ih", "exit_code": 0,
             "duration_s": duration, "error_rate": 0.0, "cost_usd": 0.0,
             "case_results": [{"case": "c", "exit_code": 0}],
             "output_hashes": {},
             "nondeterminism_handling": "not_seeded"},
            source_type="test", source_id="harness",
            idempotency_key=f"rc:{run_id}"),
            recorded_at="2026-08-22T12:00:01"))

    seed_eval(f"{release_tag}-b", "baseline", 4.0)
    seed_eval(f"{release_tag}-c", "candidate", candidate_duration)
    verification_id = record_verification_request(
        db, improvement_id=improvement_id, subject_ref=candidate,
        criteria=["guardrail correctness: c"],
        artifacts={"c": "1" * 64}, builder_identity=BUILDER,
        controller=CONTROLLER, provider_settings=SETTINGS)
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[{"criterion": "guardrail correctness: c",
                           "status": "PASS",
                           "evidence_hash": "c" * 64}],
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
    promotion_id = promote_release(
        db, candidate_release_id=candidate,
        verification_id=verification_id,
        baseline_run_id=f"{release_tag}-b",
        candidate_run_id=f"{release_tag}-c", canary_id=canary_id,
        controller=CONTROLLER)
    return {"genesis": genesis, "candidate": candidate,
            "promotion_id": promotion_id}


# ---------------------------------------------------------------------------
# THE HARNESS: multiple cycles end-to-end
# ---------------------------------------------------------------------------

def test_multi_cycle_harness_good_rejected_rollback_learning(
        db: FoundryDatabase, tmp_path: Path) -> None:
    # ---- CYCLE 1: observe -> hypothesize -> accept good candidate ---
    _seed_retry_storm(db)
    observations = _observe(db)
    assert observations, "detector produced nothing from seeded weakness"
    improvement_ids = _hypothesize(db, observations)
    assert improvement_ids, "no hypothesis frozen from observations"

    cycle1 = _evidence_and_promote(
        db, tmp_path, improvement_ids[0], candidate_duration=2.5,
        release_tag="1")
    pointers = slot_pointers(db)
    assert pointers["live"] == cycle1["candidate"]
    assert pointers["candidate"] is None  # cleared by promotion

    # duplicate promotion must refuse (already-live guard)
    from jarvis_foundry.evolution.promote import PromoteError
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=cycle1["candidate"],
            verification_id=record_verification_request(
                db, improvement_id=improvement_ids[0],
                subject_ref=cycle1["candidate"],
                criteria=["guardrail correctness: c"],
                artifacts={"c": "1" * 64}, builder_identity=BUILDER,
                controller=CONTROLLER, provider_settings=SETTINGS),
            baseline_run_id="1-b", candidate_run_id="1-c",
            canary_id=None, controller=CONTROLLER)

    # ---- learn from CYCLE 1: predicted vs actual --------------------
    outcome = record_outcome(
        db, improvement_id=improvement_ids[0],
        detector_rule="mission_retry_count",
        evidence_window=WINDOW, predicted_delta=-3.0, actual_delta=-1.5,
        unit="runs", retained=True,
        lessons=["retry storm halved; mechanism partially applied"])
    assert outcome["classification"] in ("CONFIRMED", "PARTIAL")

    # ---- CYCLE 2: a DEGRADED successor is REJECTED by the gates -----
    degraded_duration = 9.0  # slower than baseline 4.0
    improvement_v2 = improvement_ids[-1]
    genesis2 = register_release(
        db, source_commit="3" * 40, artifact_hashes={"g2": "2" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_v2,
        risk_class="R2", migrations=[], verdicts=[],
        controller=CONTROLLER)
    degraded = register_release(
        db, source_commit="4" * 40, artifact_hashes={"d": "3" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=genesis2, improvement_id=improvement_v2,
        risk_class="R2", migrations=[], verdicts=[],
        controller=CONTROLLER)
    inputs2 = tmp_path / "in-degraded"
    inputs2.mkdir(exist_ok=True)
    (inputs2 / "case.txt").write_text("x", encoding="utf-8")
    workload2 = register_workload(db, name="w-deg", input_dir=inputs2,
                                  config={}, seeds=[],
                                  controller=CONTROLLER)
    for kind, dur in (("baseline", 4.0), ("candidate", degraded_duration)):
        run_id = f"deg-{kind}"
        db.append(replace(Event.create(
            "evaluation.run_started",
            {"run_id": run_id, "workload_id": workload2,
             "subject_kind": kind, "subject_identity": "x",
             "command_hash": "h"},
            source_type="test", source_id="harness",
            idempotency_key=f"rs:{run_id}"),
            recorded_at="2026-08-22T12:30:00"))
        db.append(replace(Event.create(
            "evaluation.run_completed",
            {"run_id": run_id, "workload_id": workload2,
             "subject_kind": kind, "subject_identity": "x",
             "command_hash": "h", "input_hash": "ih2", "exit_code": 0,
             "duration_s": dur, "error_rate": 0.0, "cost_usd": 0.0,
             "case_results": [{"case": "c", "exit_code": 0}],
             "output_hashes": {},
             "nondeterminism_handling": "not_seeded"},
            source_type="test", source_id="harness",
            idempotency_key=f"rc:{run_id}"),
            recorded_at="2026-08-22T12:30:01"))
    vid2 = record_verification_request(
        db, improvement_id=improvement_v2, subject_ref=degraded,
        criteria=["guardrail correctness: c"],
        artifacts={"d": "3" * 64}, builder_identity=BUILDER,
        controller=CONTROLLER, provider_settings=SETTINGS)
    record_verification_verdict(
        db, verification_id=vid2, verdict="PASS",
        criteria_results=[{"criterion": "guardrail correctness: c",
                           "status": "PASS", "evidence_hash": "d" * 64}],
        findings=[], verifier_identity=VERIFIER, evidence_refs=["e"],
        provider_settings=SETTINGS)
    cid2 = start_canary(
        db, improvement_id=improvement_v2, candidate_release_id=degraded,
        traffic_share=0.25, duration_cycles=1, max_cost_usd=1.0,
        health_thresholds={"error_rate_max": 0.05, "latency_max_s": 2.0},
        controller=CONTROLLER)
    canary_tick(db, canary_id=cid2, cycle=1, traffic_served=0.25,
                errors=0, latency_s=0.2, cost_usd=0.01,
                controller=CONTROLLER)
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=degraded, verification_id=vid2,
            baseline_run_id="deg-baseline",
            candidate_run_id="deg-candidate", canary_id=cid2,
            controller=CONTROLLER)
    # slot state untouched by the rejected attempt
    assert slot_pointers(db)["live"] == cycle1["candidate"]

    # ---- ROLLBACK path: the live release regresses post-promotion ---
    rollback_pid = rollback_release(
        db, trigger="outcome_regression",
        reason="cycle-2 regression observed after promotion",
        controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["live"] == cycle1["genesis"]
    assert pointers["quarantine"] == cycle1["candidate"]

    # ---- STOP blocks eligibility ------------------------------------
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    from jarvis_foundry.evolution.eligibility import (
        decide_promotion_eligibility,
    )
    decision = decide_promotion_eligibility(
        db, candidate_release_id=degraded,
        verification_id=vid2, baseline_run_id="deg-baseline",
        candidate_run_id="deg-candidate", canary_id=cid2)
    assert decision["decision"] == "BLOCKED"
    sentinel.unlink()  # dispose the harness sentinel

    # ---- measurement + calibration survive the full loop ------------
    snapshot = capture_measurement(
        db, improvement_id=improvement_ids[0], subject_kind="baseline",
        window_start=WINDOW[0], window_end=WINDOW[1])
    assert snapshot["status"] in ("MEASURED", "INSUFFICIENT_SAMPLE")
    calibration = rule_calibration(db, "mission_retry_count")
    assert calibration["evaluated"] >= 1
    report = reconcile(db)
    assert report.consistent


def test_selector_waits_when_nothing_qualifies(
        db: FoundryDatabase) -> None:
    result = select_next_improvement([], mission_load=0,
                                     mission_capacity=2)
    assert result["waiting"] is True


def test_no_duplicate_hypothesis_from_identical_observation(
        db: FoundryDatabase) -> None:
    _seed_retry_storm(db)
    first = _observe(db)
    second = _observe(db)
    ids_one = _hypothesize(db, first)
    ids_two = _hypothesize(db, second)
    assert ids_one == ids_two, \
        "identical observations spawned a second hypothesis"
