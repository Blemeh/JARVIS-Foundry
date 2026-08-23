"""V6 lane: typed benchmark/evaluation engine.

Covers: full metric table (latency/error/cost/pass-rate + honest
NOT_AVAILABLE row), incomparable-run refusals, content-derived
benchmark ids, guardrail failure surfacing, governed comparison
agreement (improvement_met), and reconcile consistency.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.benchmark import (
    BenchmarkError,
    benchmark_by_id,
    build_benchmark,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile

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
        problem="p", evidence_refs=["e"], provenance={"source": "test"},
        hypothesis="h", expected_mechanism="m",
        baseline_metric={"name": "suite_runtime_s", "value": 4.0,
                         "unit": "s", "source": "e"},
        mmi={"minimum_delta": 1.0, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "no failures", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "w1", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.5, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})
    kwargs.update(overrides)
    return record_improvement(db, **kwargs)


def _seed_run(db: FoundryDatabase, run_id: str, workload_id: str,
              subject_kind: str, *, duration_s: float = 4.0,
              error_rate: float = 0.0, cost_usd: float = 0.0,
              case_results: list | None = None,
              input_hash: str = "ih") -> None:
    event = Event.create(
        "evaluation.run_started",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": subject_kind, "subject_identity": "x",
         "command_hash": "h"},
        source_type="test", source_id="bench",
        idempotency_key=f"ers:{run_id}")
    db.append(replace(event, recorded_at="2026-08-22T12:00:00"))
    completed = Event.create(
        "evaluation.run_completed",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": subject_kind, "subject_identity": "x",
         "command_hash": "h", "input_hash": input_hash,
         "exit_code": 0 if error_rate == 0 else 1,
         "duration_s": duration_s, "error_rate": error_rate,
         "cost_usd": cost_usd,
         "case_results": case_results if case_results is not None else [
             {"case": "c1", "exit_code":
                 0 if error_rate == 0 else 1}],
         "output_hashes": {},
         "nondeterminism_handling": "not_seeded"},
        source_type="test", source_id="bench",
        idempotency_key=f"erc:{run_id}")
    db.append(replace(completed, recorded_at="2026-08-22T12:00:01"))


def _metric(payload: dict, name: str) -> dict:
    return next(m for m in payload["metrics"]
                if m["metric"] == name)


def test_full_metric_table_recorded(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "b1", "w1", "baseline", duration_s=4.0)
    _seed_run(db, "c1", "w1", "candidate", duration_s=2.5)
    payload = build_benchmark(db, improvement_id=improvement_id,
                              baseline_run_id="b1",
                              candidate_run_id="c1")
    latency = _metric(payload, "latency_s")
    assert latency["baseline"] == 4.0 and latency["candidate"] == 2.5
    assert latency["delta"] == -1.5
    assert latency["met"] is True  # decrease by >= mmi 1.0
    error = _metric(payload, "error_rate")
    assert error["baseline"] == 0.0 and error["candidate"] == 0.0
    assert payload["improvement_met"] is True
    assert payload["guardrail_failures"] == []
    replay = _metric(payload, "mission_replay_quality")
    assert replay["status"] == "NOT_AVAILABLE"
    assert replay["met"] is None


def test_incomparable_runs_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "b1", "w1", "baseline")
    with pytest.raises(BenchmarkError):
        build_benchmark(db, improvement_id=improvement_id,
                        baseline_run_id="b1", candidate_run_id="c-missing")


def test_wrong_subject_kinds_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "bb", "w1", "baseline")
    _seed_run(db, "bc", "w1", "baseline")  # wrong kind for candidate
    with pytest.raises(BenchmarkError, match="not a candidate run"):
        build_benchmark(db, improvement_id=improvement_id,
                        baseline_run_id="bb", candidate_run_id="bc")


def test_different_input_hashes_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "hb", "w1", "baseline", input_hash="ih-a")
    _seed_run(db, "hc", "w1", "candidate", input_hash="ih-b")
    with pytest.raises(BenchmarkError, match="input hashes"):
        build_benchmark(db, improvement_id=improvement_id,
                        baseline_run_id="hb", candidate_run_id="hc")


def test_guardrail_failures_surfaced_not_averaged(
        db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "gb", "w1", "baseline", duration_s=4.0,
              cost_usd=0.10)
    # candidate slower AND failing cases AND over budget
    _seed_run(db, "gc", "w1", "candidate", duration_s=6.0,
              error_rate=0.5, cost_usd=2.0)
    payload = build_benchmark(db, improvement_id=improvement_id,
                              baseline_run_id="gb",
                              candidate_run_id="gc")
    assert payload["improvement_met"] is False
    assert "quality" in payload["guardrail_failures"]
    assert "cost" in payload["guardrail_failures"]
    latency = _metric(payload, "latency_s")
    assert latency["met"] is False


def test_case_pass_rate_from_case_results(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "pb", "w1", "baseline", case_results=[
        {"case": "a", "exit_code": 0}, {"case": "b", "exit_code": 0}])
    _seed_run(db, "pc", "w1", "candidate", case_results=[
        {"case": "a", "exit_code": 0}, {"case": "b", "exit_code": 3}])
    payload = build_benchmark(db, improvement_id=improvement_id,
                              baseline_run_id="pb",
                              candidate_run_id="pc")
    pass_rate = _metric(payload, "case_pass_rate")
    assert pass_rate["baseline"] == 1.0
    assert pass_rate["candidate"] == 0.5
    assert "correctness" in payload["guardrail_failures"]


def test_identical_pair_same_benchmark_id(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    _seed_run(db, "db", "w1", "baseline")
    _seed_run(db, "dc", "w1", "candidate")
    one = build_benchmark(db, improvement_id=improvement_id,
                          baseline_run_id="db", candidate_run_id="dc")
    two = build_benchmark(db, improvement_id=improvement_id,
                          baseline_run_id="db", candidate_run_id="dc")
    assert one["benchmark_id"] == two["benchmark_id"]
    stored = benchmark_by_id(db, one["benchmark_id"])
    assert stored is not None
    report = reconcile(db)
    assert report.consistent
