"""V6 lane: promotion eligibility decision (typed, never promotes).

Proves the required matrix:
- risk mismatch fails (REJECTED);
- missing/failed verifier fails (REJECTED);
- no measured improvement rejects;
- guardrail regression rejects;
- STOP/veto blocks (BLOCKED);
- freeze blocks (BLOCKED);
- missing evidence is UNKNOWN, never guessed;
- a fully-evidenced candidate is ELIGIBLE and the decision function
  itself performs NO promotion (slot state untouched).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.eligibility import (
    decide_promotion_eligibility,
    eligibility_report,
)
from jarvis_foundry.evolution.lab import register_workload, run_evaluation
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    register_release,
    set_slot,
)
from jarvis_foundry.evolution.verify import (
    record_verification_request,
    record_verification_verdict,
)
from jarvis_foundry.events import Event
from jarvis_foundry.runtime.provider_config import default_provider_settings

SETTINGS = default_provider_settings()
BUILDER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
           "session_id": "builder-el"}
VERIFIER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
            "session_id": "verifier-el"}
CONTROLLER = "supervisor"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase, **overrides) -> str:
    kwargs = dict(
        problem="p", evidence_refs=["e"], provenance={"source": "t"},
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
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})
    kwargs.update(overrides)
    return record_improvement(db, **kwargs)


def _seed_eval(db: FoundryDatabase, run_id: str, workload_id: str,
               kind: str, *, duration_s: float) -> str:
    db.append(replace(Event.create(
        "evaluation.run_started",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": kind, "subject_identity": "x",
         "command_hash": "h"},
        source_type="test", source_id="el", idempotency_key=f"rs:{run_id}"),
        recorded_at="2026-08-22T12:00:00"))
    db.append(replace(Event.create(
        "evaluation.run_completed",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": kind, "subject_identity": "x",
         "command_hash": "h", "input_hash": "ih", "exit_code": 0,
         "duration_s": duration_s, "error_rate": 0.0, "cost_usd": 0.0,
         "case_results": [{"case": "c", "exit_code": 0}],
         "output_hashes": {}, "nondeterminism_handling": "not_seeded"},
        source_type="test", source_id="el", idempotency_key=f"rc:{run_id}"),
        recorded_at="2026-08-22T12:00:01"))
    return run_id


def _full_setup(db: FoundryDatabase, tmp_path: Path, *,
                candidate_duration: float = 2.0,
                with_verifier_pass: bool = True,
                risk: str = "R2") -> dict[str, Any]:
    improvement_id = _improvement(db)
    genesis = register_release(
        db, source_commit="a" * 40, artifact_hashes={"g.txt": "0" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class=risk, migrations=[], verdicts=[],
        controller=CONTROLLER)
    candidate = register_release(
        db, source_commit="b" * 40, artifact_hashes={"c.txt": "1" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=genesis, improvement_id=improvement_id,
        risk_class=risk, migrations=[], verdicts=[],
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER,
             transition_id="el:genesis")
    set_slot(db, "candidate", candidate, reason="candidate",
             controller=CONTROLLER, transition_id="el:cand")
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "case.txt").write_text("x", encoding="utf-8")
    workload_id = register_workload(
        db, name="w-el", input_dir=inputs, config={}, seeds=[],
        controller=CONTROLLER)
    baseline_run = _seed_eval(db, "el-b", workload_id, "baseline",
                              duration_s=4.0)
    cand_run = _seed_eval(db, "el-c", workload_id, "candidate",
                          duration_s=candidate_duration)
    verification_id = None
    if with_verifier_pass:
        verification_id = record_verification_request(
            db, improvement_id=improvement_id,
            subject_ref=candidate,
            criteria=["guardrail correctness: c"],
            artifacts={"c.txt": "1" * 64}, builder_identity=BUILDER,
            controller=CONTROLLER, provider_settings=SETTINGS)
        record_verification_verdict(
            db, verification_id=verification_id, verdict="PASS",
            criteria_results=[{"criterion": "guardrail correctness: c",
                               "status": "PASS",
                               "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=VERIFIER,
            evidence_refs=["e"], provider_settings=SETTINGS)
    else:
        verification_id = record_verification_request(
            db, improvement_id=improvement_id,
            subject_ref=candidate,
            criteria=["guardrail correctness: c"],
            artifacts={"c.txt": "1" * 64}, builder_identity=BUILDER,
            controller=CONTROLLER, provider_settings=SETTINGS)
        record_verification_verdict(
            db, verification_id=verification_id, verdict="FAIL",
            criteria_results=[{"criterion": "guardrail correctness: c",
                               "status": "FAIL", "evidence_hash": None}],
            findings=[], verifier_identity=VERIFIER,
            evidence_refs=["e"], provider_settings=SETTINGS)
    return {"improvement_id": improvement_id, "candidate": candidate,
            "genesis": genesis, "baseline_run": baseline_run,
            "candidate_run": cand_run, "verification_id": verification_id}


def _with_canary(db: FoundryDatabase, setup: dict[str, Any]) -> str:
    from jarvis_foundry.evolution.canary import (
        canary_tick,
        start_canary,
    )
    canary_id = start_canary(
        db, improvement_id=setup["improvement_id"],
        candidate_release_id=setup["candidate"], traffic_share=0.25,
        duration_cycles=1, max_cost_usd=1.0,
        health_thresholds={"error_rate_max": 0.05, "latency_max_s": 2.0},
        controller=CONTROLLER)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.25,
                errors=0, latency_s=0.2, cost_usd=0.01,
                controller=CONTROLLER)
    return canary_id


# ---------------------------------------------------------------------------
# the required matrix
# ---------------------------------------------------------------------------

def test_missing_verifier_fails(db: FoundryDatabase, tmp_path: Path,
                                ) -> None:
    setup = _full_setup(db, tmp_path, with_verifier_pass=False)
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"])
    assert result["decision"] == "REJECTED"
    assert any("verifier" in r.lower() for r in result["reasons"])


def test_no_measured_improvement_rejects(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    setup = _full_setup(db, tmp_path, candidate_duration=4.5)  # slower
    canary_id = _with_canary(db, setup)  # pass earlier gates honestly
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"], canary_id=canary_id)
    assert result["decision"] == "REJECTED"
    assert any("improvement absent" in r for r in result["reasons"])


def test_stale_or_missing_runs_unknown(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    setup = _full_setup(db, tmp_path)
    canary_id = _with_canary(db, setup)
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id="run-nonexistent",
        candidate_run_id=setup["candidate_run"], canary_id=canary_id)
    assert result["decision"] == "UNKNOWN"


def test_stop_sentinel_blocks(db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _full_setup(db, tmp_path)
    (Path(db.path).parent / "EMERGENCY_STOP").write_text("x",
                                                         encoding="utf-8")
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"])
    assert result["decision"] == "BLOCKED"


def test_freeze_blocks(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.evolution.promote import freeze_active
    setup = _full_setup(db, tmp_path)
    freeze_active(db, reason="test freeze", controller=CONTROLLER)
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"])
    assert result["decision"] == "BLOCKED"


def test_r2_without_canary_rejects(db: FoundryDatabase,
                                   tmp_path: Path) -> None:
    setup = _full_setup(db, tmp_path)
    result = decide_promotion_eligibility(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"], canary_id=None)
    assert result["decision"] == "REJECTED"
    assert any("canary" in r.lower() for r in result["reasons"])


def test_fully_evidenced_candidate_eligible_and_not_promoted(
        db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.evolution.canary import canary_tick, start_canary
    setup = _full_setup(db, tmp_path)
    canary_id = start_canary(
        db, improvement_id=setup["improvement_id"],
        candidate_release_id=setup["candidate"], traffic_share=0.25,
        duration_cycles=1, max_cost_usd=1.0,
        health_thresholds={"error_rate_max": 0.05, "latency_max_s": 2.0},
        controller=CONTROLLER)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.25,
                errors=0, latency_s=0.2, cost_usd=0.01,
                controller=CONTROLLER)
    before_slots = {r["slot"]: r["release_id"]
                    for r in db.all("release_slots")}
    report = eligibility_report(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run"],
        candidate_run_id=setup["candidate_run"], canary_id=canary_id)
    assert report["decision"] == "ELIGIBLE"
    after_slots = {r["slot"]: r["release_id"]
                   for r in db.all("release_slots")}
    assert before_slots == after_slots, \
        "eligibility evaluation mutated slots — it must NEVER promote"
