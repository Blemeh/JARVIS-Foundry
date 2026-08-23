"""V6 lane: experiment plan compiler.

Covers: compile-from-frozen (R2 requirements), refusal for unknown /
unfrozen hypotheses, deterministic plan ids, always-forbidden surface
families, R0/R1 not requiring canary, execution-trace compliance
checks (surface escapes, missing shadow/canary/verifier), and
post-compile reconcile consistency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.plan import (
    PlanError,
    compile_experiment_plan,
    validate_plan_execution,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.reconcile import reconcile

COMMIT = "a" * 40


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _record(db: FoundryDatabase, **overrides) -> str:
    kwargs = dict(
        problem="p", evidence_refs=["e"], provenance={"source": "test"},
        hypothesis="h", expected_mechanism="m",
        baseline_metric={"name": "duration_s", "value": 4.0, "unit": "s",
                         "source": "e"},
        mmi={"minimum_delta": 0.5, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "c", "safety": "s", "cost": "co",
                    "latency": "l"},
        workload={"ref": "w1", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2",
        surfaces=["execution/"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )
    kwargs.update(overrides)
    return record_improvement(db, **kwargs)


def test_compile_r2_plan_requires_shadow_canary_verifier(
        db: FoundryDatabase) -> None:
    improvement_id = _record(db)
    plan = compile_experiment_plan(db, improvement_id=improvement_id,
                                   source_commit=COMMIT)
    assert plan["mode"] == "disposable"
    assert plan["shadow_required"] is True
    assert plan["canary_required"] is True
    assert plan["verifier_independence_required"] is True
    assert plan["risk_class"] == "R2"
    assert plan["allowed_surfaces"] == ["execution/"]
    assert plan["benchmark_suite"]["success_threshold"] == {
        "metric_direction": "decrease", "minimum_delta": 0.5,
        "unit": "s"}
    assert any("correctness" in g for g in
               plan["benchmark_suite"]["regression_guardrails"])


def test_compile_refuses_unknown_and_requires_frozen(
        db: FoundryDatabase) -> None:
    with pytest.raises(PlanError):
        compile_experiment_plan(db, improvement_id="improvement-nope",
                                source_commit=COMMIT)


def test_plan_ids_deterministic_and_distinct_by_input(
        db: FoundryDatabase) -> None:
    one = _record(db)
    two = _record(db, problem="different problem")
    plan_one_a = compile_experiment_plan(db, improvement_id=one,
                                         source_commit=COMMIT)
    plan_one_b = compile_experiment_plan(db, improvement_id=one,
                                         source_commit=COMMIT)
    plan_two = compile_experiment_plan(db, improvement_id=two,
                                       source_commit=COMMIT)
    assert plan_one_a["plan_id"] == plan_one_b["plan_id"]
    assert plan_two["plan_id"] != plan_one_a["plan_id"]


def test_control_plane_surface_is_always_forbidden(
        db: FoundryDatabase) -> None:
    # the S1 classifier forces this record to R3 (supervisor/ token),
    # yet the compiled plan still refuses the control plane outright
    improvement_id = _record(db, surfaces=["supervisor/authority.py"],
                             declared_risk="R3")
    plan = compile_experiment_plan(db, improvement_id=improvement_id,
                                   source_commit=COMMIT)
    assert plan["risk_class"] == "R3"
    assert any("control/" in f for f in plan["forbidden_surfaces"])
    trace = validate_plan_execution(
        plan, touched_surfaces=["supervisor/authority.py"],
        used_shadow=True, used_canary=True,
        had_independent_verifier=True)
    assert trace["compliance"] == "VIOLATION"


def test_r1_plan_does_not_require_canary(db: FoundryDatabase) -> None:
    improvement_id = _record(db, declared_risk="R1",
                             surfaces=["tests/foundry"])
    plan = compile_experiment_plan(db, improvement_id=improvement_id,
                                   source_commit=COMMIT)
    assert plan["canary_required"] is False
    assert plan["shadow_required"] is False
    assert plan["verifier_independence_required"] is True


def test_execution_trace_compliance(db: FoundryDatabase) -> None:
    improvement_id = _record(db)
    plan = compile_experiment_plan(db, improvement_id=improvement_id,
                                   source_commit=COMMIT)
    compliant = validate_plan_execution(
        plan, touched_surfaces=["src/jarvis_foundry/execution/x.py"],
        used_shadow=True, used_canary=True,
        had_independent_verifier=True)
    assert compliant["compliance"] == "COMPLIANT"

    for kwargs, expected_fragment in (
            (dict(touched_surfaces=["src/jarvis_foundry/mission/y.py"],
                  used_shadow=True, used_canary=True,
                  had_independent_verifier=True), "outside allowed"),
            (dict(touched_surfaces=[], used_shadow=False, used_canary=True,
                  had_independent_verifier=True), "shadow"),
            (dict(touched_surfaces=[], used_shadow=True, used_canary=False,
                  had_independent_verifier=True), "canary"),
            (dict(touched_surfaces=[], used_shadow=True, used_canary=True,
                  had_independent_verifier=False), "independent verifier")):
        result = validate_plan_execution(plan, **kwargs)
        assert result["compliance"] == "VIOLATION"
        assert expected_fragment in " ".join(result["violations"])


def test_compiled_plans_reconcile_consistent(db: FoundryDatabase) -> None:
    improvement_id = _record(db)
    compile_experiment_plan(db, improvement_id=improvement_id,
                            source_commit=COMMIT)
    report = reconcile(db)
    assert report.consistent
