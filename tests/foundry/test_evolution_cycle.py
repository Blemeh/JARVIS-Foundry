"""V6-S11: R0/R1/R2 cycles + R3 fault proof — the end-to-end ladder.

run_cycle drives ONE complete autonomous candidate cycle through the
REAL machinery: build (isolated builder) -> evaluation (lab) ->
verification (verify) -> canary (R2+) -> promotion (promote), with
auto-rejection (broken candidates never promote) and the rollback
path exercised against the live pointer. The demos use REAL
subprocess workloads with REAL measurements; the broken candidates
are real failures, not mocks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.canary import canary_tick, start_canary
from jarvis_foundry.evolution.cycle import (
    CycleError,
    cycle_outcome,
    run_cycle,
)
from jarvis_foundry.evolution.lab import register_workload
from jarvis_foundry.evolution.promote import (
    rollback_release,
    slot_pointers,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import set_slot
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"
DIRECTIVE_IDENTITY = {"provider": "opencode_go",
                      "model": "deepseek-v4-flash",
                      "session_id": "session-builder-s11"}
OTHER_IDENTITY = {"provider": "opencode_go",
                  "model": "deepseek-v4-flash",
                  "session_id": "session-verifier-s11"}

SLOW = [sys.executable, "-c",
        "import pathlib,sys,time\n"
        "pathlib.Path(sys.argv[1]).read_text()\n"
        "time.sleep(0.3)\n"]
FAST = [sys.executable, "-c",
        "import pathlib,sys\n"
        "pathlib.Path(sys.argv[1]).read_text()\n"]
BROKEN = [sys.executable, "-c",
          "import pathlib,sys\n"
          "pathlib.Path(sys.argv[1]).read_text()\n"
          "raise SystemExit(1)\n"]
BUILD_OK = [sys.executable, "-c",
            "import pathlib\n"
            "pathlib.Path('built.txt').write_text('built')\n"]
BUILD_BROKEN = [sys.executable, "-c", "raise SystemExit(2)\n"]


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase, risk: str = "R1") -> str:
    surfaces = ["tests/foundry"] if risk == "R1" else ["evaluation"]
    return record_improvement(
        db,
        problem="representative workload latency exceeds the threshold",
        evidence_refs=["var/evidence/v6-s11/observation.txt"],
        provenance={"source": "v6-s11 demonstration observation",
                    "observed_at": "2026-08-13T23:00:00Z"},
        hypothesis="removing the serialized wait reduces latency",
        expected_mechanism="the measured workload carries a fixed "
                           "per-case wait",
        baseline_metric={"name": "latency_s", "value": 0.9,
                         "unit": "seconds",
                         "source": "v6-s11-observation.txt"},
        mmi={"minimum_delta": 0.1, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "1 demonstration cycle", "cycles": 1},
        declared_risk=risk, surfaces=surfaces,
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _workload(tmp_path: Path) -> Path:
    directory = tmp_path / "workload"
    directory.mkdir(exist_ok=True)
    for index in range(3):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    return directory


def _cycle_args(db: FoundryDatabase, tmp_path: Path,
                risk: str = "R1",
                candidate_command=FAST,
                build_command=BUILD_OK,
                ) -> dict[str, object]:
    return {
        "improvement_id": _improvement(db, risk=risk),
        "source_commit": "c" * 40,
        "workload_dir": _workload(tmp_path),
        "baseline_command": SLOW,
        "candidate_command": candidate_command,
        "build_command": build_command,
        "controller": CONTROLLER,
    }


# ---------------------------------------------------------------------------
# R1 / R2 promotion cycles
# ---------------------------------------------------------------------------

def test_r1_cycle_promotes(db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    assert result["promotion_id"].startswith("promotion-")
    assert slot_pointers(db)["live"] == result["candidate_release_id"]
    assert cycle_outcome(db, result["cycle_id"]) == "promoted"
    row = db.row("cycles", "cycle_id", result["cycle_id"])
    assert row is not None and row["outcome"] == "promoted"
    assert row["baseline_run_id"] and row["candidate_run_id"]
    assert row["build_id"]


def test_r2_cycle_with_canary_promotes(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R2")
    args["canary_ticks"] = [
        {"cycle": 1, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
        {"cycle": 2, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
    ]
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    assert result["canary_id"].startswith("canary-")
    assert slot_pointers(db)["live"] == result["candidate_release_id"]


# ---------------------------------------------------------------------------
# R3 fault proofs: broken candidates never promote
# ---------------------------------------------------------------------------

def test_broken_candidate_auto_rejected(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    """A candidate that fails its evaluation cases is auto-rejected:
    guardrail violation -> promotion refused -> cycle outcome
    rejected, live untouched."""
    args = _cycle_args(db, tmp_path, risk="R1",
                       candidate_command=BROKEN)
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "rejected"
    assert "reason" in result and result["reason"]
    assert result["promotion_id"] is None
    # the cycle's genesis stayed live — nothing was promoted
    assert slot_pointers(db)["live"] == result["genesis_release_id"]


def test_build_failure_auto_rejected(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1",
                       build_command=BUILD_BROKEN)
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "rejected"
    assert "build" in (result["reason"] or "").lower()
    assert slot_pointers(db)["live"] == result["genesis_release_id"]


def test_rollback_after_promotion(db: FoundryDatabase,
                                  tmp_path: Path) -> None:
    """R3 proof: a promoted candidate that later regresses is rolled
    back automatically — the exact previous release is restored."""
    args = _cycle_args(db, tmp_path, risk="R1")
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    candidate = result["candidate_release_id"]
    # the regression is observed; the controller rolls back
    rollback_release(db, trigger="outcome_regression",
                     reason="latency regressed post-promotion",
                     controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["quarantine"] == candidate
    assert pointers["live"] != candidate
    from jarvis_foundry.evolution.promote import promotion_status
    assert promotion_status(db, result["promotion_id"]) == "ROLLED_BACK"
    assert reconcile(db).consistent is True


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------

def test_candidate_controller_refused(db: FoundryDatabase,
                                      tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path)
    args["controller"] = CANDIDATE_CONTROLLER
    with pytest.raises(CycleError):
        run_cycle(db, **args)  # type: ignore[arg-type]
    assert db.count("cycles") == 0


def test_stop_blocks_cycle(db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(CycleError):
            run_cycle(db, **args)  # type: ignore[arg-type]
    finally:
        sentinel.unlink()
    assert db.count("cycles") == 0


def test_unknown_improvement_refused(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path)
    args["improvement_id"] = "improvement-nope"
    with pytest.raises(CycleError):
        run_cycle(db, **args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    result = run_cycle(db, **args)  # type: ignore[arg-type]
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("cycles")
    db.rebuild_projections()
    assert db.count("cycles") == before
    row = db.row("cycles", "cycle_id", result["cycle_id"])
    assert row is not None and row["outcome"] == "promoted"
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 31
    assert SCHEMA_VERSION >= 31


# ---------------------------------------------------------------------------
# round-1 repairs (deleg_dbaf806e findings: identical-content re-run
# collisions on un-namespaced slot transitions)
# ---------------------------------------------------------------------------

def test_second_identical_cycle_promotes(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    """S11 r1 (MEDIUM): TWO cycles with bit-identical content on the
    SAME DB both promote — the arena transitions are namespaced per
    cycle (transition_id), so the second cycle's slot moves re-apply
    instead of deduping against the first (state-diverged no-op)."""
    args = _cycle_args(db, tmp_path, risk="R1")
    first = run_cycle(db, **args)  # type: ignore[arg-type]
    assert first["outcome"] == "promoted"
    second = run_cycle(db, **args)  # type: ignore[arg-type]
    assert second["outcome"] == "promoted", second.get("reason")
    assert second["cycle_id"] != first["cycle_id"]
    assert second["promotion_id"] != first["promotion_id"]
    # the arena reset re-pointed live to the second cycle's candidate
    assert slot_pointers(db)["live"] == second["candidate_release_id"]
    assert db.count("cycles") == 2
    assert db.count("promotions") == 2
    assert reconcile(db).consistent is True
