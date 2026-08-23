"""V6 lane: disposable canary execution (no live production).

Covers: bounded deterministic allocation, healthy candidate completes,
regressing candidate auto-terminates with reason, budget stop,
post-termination tick refusal (candidate cannot self-extend),
STOP sentinel immediate termination, and reconcile consistency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.disposable_canary import (
    allocate_traffic,
    assert_candidate_cannot_self_extend,
    run_disposable_canary,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import register_release


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _candidate(db: FoundryDatabase) -> tuple[str, str]:
    improvement_id = record_improvement(
        db, problem="p", evidence_refs=["e"],
        provenance={"source": "test"}, hypothesis="h",
        expected_mechanism="m",
        baseline_metric={"name": "suite_runtime_s", "value": 4.0,
                         "unit": "s", "source": "e"},
        mmi={"minimum_delta": 1.0, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "c", "safety": "s", "cost": "co",
                    "latency": "l"},
        workload={"ref": "w", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})
    release_id = register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    return improvement_id, release_id


def _workload(count: int = 40, cost: float = 0.01) -> list[dict]:
    return [{"cost_usd": cost} for _ in range(count)]


def _healthy(item: dict) -> dict:
    return {"error": False, "latency_s": 0.2}


def _healthy_baseline(item: dict) -> dict:
    return {"error": False, "latency_s": 0.3}


# ---------------------------------------------------------------------------
# allocation bounds
# ---------------------------------------------------------------------------

def test_allocation_is_deterministic_and_bounded() -> None:
    routed = sum(1 for i in range(1000)
                 if allocate_traffic(i, traffic_share=0.25))
    assert 200 <= routed <= 300, f"share not honored: {routed}"
    again = [allocate_traffic(i, traffic_share=0.25)
             for i in range(1000)]
    assert sum(again) == routed, "allocation is not deterministic"


def test_full_share_routes_everything() -> None:
    assert all(allocate_traffic(i, traffic_share=1.0) for i in range(50))


# ---------------------------------------------------------------------------
# execution paths
# ---------------------------------------------------------------------------

def test_healthy_candidate_completes(db: FoundryDatabase) -> None:
    improvement_id, release_id = _candidate(db)
    report = run_disposable_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        workload=_workload(), candidate_behavior_fn=_healthy,
        baseline_behavior_fn=_healthy_baseline,
        traffic_share=0.5, duration_cycles=2)
    assert report["status"] == "COMPLETED"
    assert report["candidate"]["error_rate"] == 0.0


def test_regressing_candidate_auto_terminates(
        db: FoundryDatabase) -> None:
    improvement_id, release_id = _candidate(db)

    def broken(item: dict) -> dict:
        return {"error": True, "latency_s": 0.2}

    report = run_disposable_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        workload=_workload(), candidate_behavior_fn=broken,
        baseline_behavior_fn=_healthy_baseline,
        traffic_share=0.5, duration_cycles=3)
    assert report["status"] == "TERMINATED"
    assert report["stop_reason"] is not None
    assert report["candidate"]["error_rate"] > 0.0


def test_budget_stop_terminates(db: FoundryDatabase) -> None:
    improvement_id, release_id = _candidate(db)
    report = run_disposable_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        # 40 items x 0.5 cost = 20.0 >> budget 1.0
        workload=_workload(cost=0.5), candidate_behavior_fn=_healthy,
        baseline_behavior_fn=_healthy_baseline,
        traffic_share=0.5, duration_cycles=2, max_cost_usd=1.0)
    row = db.row("canaries", "canary_id", report["canary_id"])
    assert row["status"] == "TERMINATED"
    assert "cost" in str(row["reason"]).lower()


def test_stop_sentinel_blocks_start_and_terminates_ticks(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id, release_id = _candidate(db)
    # start BEFORE the sentinel exists
    from jarvis_foundry.evolution.canary import start_canary
    canary_id = start_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, traffic_share=0.5,
        duration_cycles=3, max_cost_usd=1.0,
        health_thresholds={"error_rate_max": 0.05, "latency_max_s": 2.0},
        controller="supervisor")
    # STOP lands mid-run: the very next tick must terminate immediately
    (tmp_path / "runtime" / "EMERGENCY_STOP").write_text(
        "stop", encoding="utf-8")
    from jarvis_foundry.evolution.canary import canary_tick
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.5, errors=0, latency_s=0.2,
                         cost_usd=0.01, controller="supervisor")
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert "stop" in str(row["reason"]).lower()


def test_candidate_cannot_self_extend_after_stop(
        db: FoundryDatabase) -> None:
    improvement_id, release_id = _candidate(db)
    report = run_disposable_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        workload=_workload(), candidate_behavior_fn=_healthy,
        baseline_behavior_fn=_healthy_baseline,
        traffic_share=0.5, duration_cycles=2)
    assert report["status"] == "COMPLETED"
    assert_candidate_cannot_self_extend(db, report["canary_id"])


def test_disposable_runs_reconcile_consistent(db: FoundryDatabase) -> None:
    from jarvis_foundry.reconcile import reconcile
    improvement_id, release_id = _candidate(db)
    run_disposable_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        workload=_workload(), candidate_behavior_fn=_healthy,
        baseline_behavior_fn=_healthy_baseline,
        traffic_share=0.5, duration_cycles=2)
    report = reconcile(db)
    assert report.consistent
