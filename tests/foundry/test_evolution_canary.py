"""V6-S7: Supervisor-owned canary controller.

Live canaries are bounded in traffic share, duration, budget, health
thresholds and promotion eligibility — and the SUPERVISOR (controller)
owns start, tick allocation and termination. A candidate can never
start, extend, expand or terminate its own canary; STOP and vetoes
terminate immediately; termination works WITHOUT candidate cooperation
(hung candidates are reaped); health is recomputed from raw metrics
(never trusting reported flags); cost spikes, regressions and invariant
breaches terminate automatically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.canary import (
    CanaryError,
    canary_completed_successfully,
    canary_status,
    canary_tick,
    reap_stale_canaries,
    start_canary,
    terminate_canary,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import register_release
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"
THRESHOLDS = {"error_rate_max": 0.05, "latency_max_s": 2.0}


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="suite runtime exceeds the 5-minute budget on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="Splitting fault-injection tests reduces wall-clock "
                   "runtime",
        expected_mechanism="parallel session isolation removes serialized "
                           "daemon-spawn waits",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _release(db: FoundryDatabase, improvement_id: str) -> str:
    return register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[1, 2, 3], verdicts=[],
        controller="supervisor")


def _start(db: FoundryDatabase, improvement_id: str, release_id: str,
           **overrides: object) -> str:
    params: dict[str, object] = {
        "traffic_share": 0.05, "duration_cycles": 5,
        "max_cost_usd": 1.0, "health_thresholds": THRESHOLDS,
        "controller": CONTROLLER,
    }
    params.update(overrides)
    return start_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, **params)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# controller ownership and start bounds
# ---------------------------------------------------------------------------

def test_start_canary_controller_owned(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    assert canary_id.startswith("canary-")
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "RUNNING"
    assert row["traffic_share"] == 0.05
    assert row["duration_cycles"] == 5
    assert row["max_cost_usd"] == 1.0
    assert canary_status(db, canary_id) == "RUNNING"


def test_candidate_cannot_start_canary(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    with pytest.raises(CanaryError):
        _start(db, improvement_id, release_id,
               controller=CANDIDATE_CONTROLLER)
    assert db.count("canaries") == 0


def test_start_bounds(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    for bad in ({"traffic_share": 0.0}, {"traffic_share": 1.5},
                {"traffic_share": -0.1}, {"duration_cycles": 0},
                {"duration_cycles": -3}, {"max_cost_usd": -1.0},
                {"health_thresholds": {}}):
        with pytest.raises(CanaryError):
            _start(db, improvement_id, release_id, **bad)  # type: ignore[arg-type]
    assert db.count("canaries") == 0


def test_start_refused_when_stop_active(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(CanaryError):
            _start(db, improvement_id, release_id)
    finally:
        sentinel.unlink()
    assert db.count("canaries") == 0


def test_unknown_improvement_or_release_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    with pytest.raises(CanaryError):
        _start(db, "improvement-nope", release_id)
    with pytest.raises(CanaryError):
        _start(db, improvement_id, "release-nope")


# ---------------------------------------------------------------------------
# ticks and health
# ---------------------------------------------------------------------------

def test_healthy_tick_progresses(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=0, latency_s=0.5,
                         cost_usd=0.01, controller=CONTROLLER)
    assert status == "RUNNING"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["cycles_run"] == 1
    assert row["last_health"] == 1
    assert row["total_cost_usd"] == 0.01


def test_false_health_terminates(db: FoundryDatabase) -> None:
    """Health is recomputed from RAW metrics — a reported healthy=True
    with error_rate above the threshold terminates the canary."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=5, latency_s=0.5,
                         cost_usd=0.01, reported_healthy=True,
                         controller=CONTROLLER)
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "health" in (row["reason"] or "")
    assert row["last_health"] == 0


def test_latency_breach_terminates(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=0, latency_s=9.9,
                         cost_usd=0.01, controller=CONTROLLER)
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "health" in (row["reason"] or "")


def test_cost_spike_terminates(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id, max_cost_usd=0.05)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=0, latency_s=0.5,
                         cost_usd=0.06, controller=CONTROLLER)
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "cost" in (row["reason"] or "")


def test_regression_terminates(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=0, latency_s=0.5,
                         cost_usd=0.01, regression_observed=True,
                         controller=CONTROLLER)
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "regression" in (row["reason"] or "")


def test_invariant_breach_terminates(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=0, latency_s=0.5,
                         cost_usd=0.01, invariant_violated=True,
                         controller=CONTROLLER)
    assert status == "TERMINATED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "invariant" in (row["reason"] or "")


def test_duration_reached_completes(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id, duration_cycles=2)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    status = canary_tick(db, canary_id=canary_id, cycle=2,
                         traffic_served=0.05, errors=0, latency_s=0.5,
                         cost_usd=0.01, controller=CONTROLLER)
    assert status == "COMPLETED"
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "COMPLETED"
    assert row["cycles_run"] == 2
    assert canary_completed_successfully(db, canary_id) is True


# ---------------------------------------------------------------------------
# termination without cooperation + candidate refusal
# ---------------------------------------------------------------------------

def test_termination_without_candidate_cooperation(
        db: FoundryDatabase) -> None:
    """A hung candidate (no ticks) cannot prevent termination: the
    controller terminates and traffic stops (ticks are refused)."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    terminate_canary(db, canary_id=canary_id,
                     reason="candidate hung — controller override",
                     controller=CONTROLLER)
    assert canary_status(db, canary_id) == "TERMINATED"
    # traffic stops without the candidate's cooperation
    with pytest.raises(CanaryError):
        canary_tick(db, canary_id=canary_id, cycle=1,
                    traffic_served=0.05, errors=0, latency_s=0.5,
                    cost_usd=0.01, controller=CONTROLLER)


def test_candidate_cannot_terminate_or_extend(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    with pytest.raises(CanaryError):
        terminate_canary(db, canary_id=canary_id, reason="self",
                         controller=CANDIDATE_CONTROLLER)
    # an attempt to extend the canary = re-starting it is refused
    with pytest.raises(CanaryError):
        _start(db, improvement_id, release_id,
               controller=CANDIDATE_CONTROLLER)
    assert canary_status(db, canary_id) == "RUNNING"


def test_stop_terminates_running_canary(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        status = canary_tick(db, canary_id=canary_id, cycle=1,
                             traffic_served=0.05, errors=0,
                             latency_s=0.5, cost_usd=0.01,
                             controller=CONTROLLER)
        assert status == "TERMINATED"
    finally:
        sentinel.unlink()
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "stop" in (row["reason"] or "").lower()


def test_stale_canary_reaped(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    reaped = reap_stale_canaries(db, current_cycle=10,
                                 max_cycles_without_tick=5,
                                 controller=CONTROLLER)
    assert canary_id in reaped
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["status"] == "TERMINATED"
    assert "stale" in (row["reason"] or "")


def test_active_canary_not_reaped(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    canary_tick(db, canary_id=canary_id, cycle=9, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    reaped = reap_stale_canaries(db, current_cycle=10,
                                 max_cycles_without_tick=5,
                                 controller=CONTROLLER)
    assert canary_id not in reaped
    assert canary_status(db, canary_id) == "RUNNING"


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("canaries")
    db.rebuild_projections()
    assert db.count("canaries") == before
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["cycles_run"] == 1
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 27
    assert SCHEMA_VERSION >= 27


# ---------------------------------------------------------------------------
# round-1 repair (deleg_b8bf3e0b finding: out-of-order cycles)
# ---------------------------------------------------------------------------

def test_out_of_order_cycle_refused(db: FoundryDatabase) -> None:
    """S7 r1 (LOW): ticks must be monotonic — a cycle not after the
    last ticked cycle is refused (a misbehaving controller cannot
    rewind last_tick_cycle or corrupt duration math)."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    canary_id = _start(db, improvement_id, release_id)
    canary_tick(db, canary_id=canary_id, cycle=5, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    with pytest.raises(CanaryError):
        canary_tick(db, canary_id=canary_id, cycle=5,
                    traffic_served=0.05, errors=0, latency_s=0.5,
                    cost_usd=0.01, controller=CONTROLLER)
    with pytest.raises(CanaryError):
        canary_tick(db, canary_id=canary_id, cycle=2,
                    traffic_served=0.05, errors=0, latency_s=0.5,
                    cost_usd=0.01, controller=CONTROLLER)
    row = db.row("canaries", "canary_id", canary_id)
    assert row is not None and row["last_tick_cycle"] == 5
    assert row["cycles_run"] == 1
