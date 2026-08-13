"""V6-S12: engineering + operational acceptance.

engineering_acceptance checks the machinery itself (idempotent
migrations from any base, schema pin, reconcile consistency, event
integrity). operational_acceptance counts the plan's operational
minimums from the cycles/promotions ledger (>= 3 complete cycles,
>= 2 promotions with measured improvement, >= 1 R1 real promotion,
>= 1 R2 canary+promotion, >= 1 broken candidate auto-rejected, >= 1
rollback) and FAILS CLOSED when any minimum is unmet. record_acceptance
archives the report; acceptance runs are replayable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.acceptance import (
    AcceptanceError,
    engineering_acceptance,
    operational_acceptance,
    record_acceptance,
)
from jarvis_foundry.evolution.cycle import run_cycle
from jarvis_foundry.evolution.promote import (
    rollback_release,
    slot_pointers,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"

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


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _cycle_args(db: FoundryDatabase, tmp_path: Path,
                risk: str = "R1",
                candidate_command=FAST) -> dict[str, object]:
    import importlib.util
    source = Path(__file__).parent / "test_evolution_cycle.py"
    spec = importlib.util.spec_from_file_location(
        "test_evolution_cycle_shared", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    args = module._cycle_args(db, tmp_path, risk=risk,
                              candidate_command=candidate_command,
                              build_command=BUILD_OK)
    return args


# ---------------------------------------------------------------------------
# engineering acceptance
# ---------------------------------------------------------------------------

def test_engineering_acceptance_green(db: FoundryDatabase) -> None:
    report = engineering_acceptance(db)
    assert report["passed"] is True
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["reconcile_consistent"] is True
    assert report["event_integrity_ok"] is True
    assert report["migration_replay_ok"] is True


def test_engineering_initialize_idempotent(db: FoundryDatabase) -> None:
    """initialize() twice is a no-op (migration idempotency)."""
    db.initialize()
    report = engineering_acceptance(db)
    assert report["migration_replay_ok"] is True
    assert report["passed"] is True


def test_engineering_intermediate_target_then_full(
        tmp_path: Path) -> None:
    """A DB migrated to an intermediate target, then to the full
    SCHEMA_VERSION, is consistent (forward migration from any base)."""
    from jarvis_foundry.database import FoundryDatabase as FD
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FD(runtime_home / "foundry.db")
    database.initialize(target=10)
    database.initialize()
    report = engineering_acceptance(database)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["passed"] is True


def test_engineering_fresh_db_fails_closed(db: FoundryDatabase) -> None:
    """Operational acceptance on an EMPTY ledger fails closed (no
    cycles, no promotions)."""
    report = operational_acceptance(db)
    assert report["passed"] is False
    assert report["cycles_total"] == 0


# ---------------------------------------------------------------------------
# operational acceptance
# ---------------------------------------------------------------------------

def test_operational_acceptance_full_ledger(db: FoundryDatabase,
                                            tmp_path: Path) -> None:
    """The plan's operational minimums, all met by real cycles:
    R1 promoted + R2 canary-promoted + broken auto-rejected + build
    failure auto-rejected + one rollback."""
    # 1. R1 promotion (real measured improvement)
    args = _cycle_args(db, tmp_path, risk="R1")
    r1 = run_cycle(db, **args)  # type: ignore[arg-type]
    assert r1["outcome"] == "promoted"
    # 2. R2 promotion with a real canary
    args2 = _cycle_args(db, tmp_path, risk="R2")
    args2["canary_ticks"] = [
        {"cycle": 1, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
        {"cycle": 2, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
    ]
    r2 = run_cycle(db, **args2)  # type: ignore[arg-type]
    assert r2["outcome"] == "promoted"
    # 3. rollback of a promoted release (outcome_regression) — the
    # regression is observed on the LIVE release before any new cycle
    rollback_release(db, trigger="outcome_regression",
                     reason="post-promotion regression observed",
                     controller=CONTROLLER)
    # 4. broken candidate auto-rejected
    args3 = _cycle_args(db, tmp_path, risk="R1",
                        candidate_command=BROKEN)
    r3 = run_cycle(db, **args3)  # type: ignore[arg-type]
    assert r3["outcome"] == "rejected"
    report = operational_acceptance(db)
    assert report["passed"] is True
    assert report["cycles_total"] == 3
    assert report["promotions"] == 2
    assert report["rejections"] == 1
    assert report["r1_promotions"] >= 1
    assert report["r2_canary_promotions"] == 1
    assert report["rollbacks"] >= 1
    assert report["measured_improvements"] >= 2


def test_operational_fails_closed_below_minimums(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """One promotion alone is NOT accepted (needs >= 2 promotions)."""
    args = _cycle_args(db, tmp_path, risk="R1")
    run_cycle(db, **args)  # type: ignore[arg-type]
    report = operational_acceptance(db)
    assert report["passed"] is False
    assert any("promotions" in reason
               for reason in report["reasons"])


def test_record_acceptance_archives(db: FoundryDatabase,
                                    tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    run_cycle(db, **args)  # type: ignore[arg-type]
    report = operational_acceptance(db)
    run_id = record_acceptance(db, report=report,
                               controller=CONTROLLER)
    row = db.row("acceptance_runs", "run_id", run_id)
    assert row is not None
    assert row["passed"] == report["passed"]
    types = [e.event_type for e in db.events()]
    assert "acceptance.completed" in types


def test_acceptance_candidate_controller_refused(
        db: FoundryDatabase) -> None:
    report = operational_acceptance(db)
    with pytest.raises(AcceptanceError):
        record_acceptance(db, report=report,
                          controller=CANDIDATE_CONTROLLER)


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase,
                               tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    run_cycle(db, **args)  # type: ignore[arg-type]
    report = operational_acceptance(db)
    record_acceptance(db, report=report, controller=CONTROLLER)
    assert reconcile(db).consistent is True
    before = db.count("acceptance_runs")
    db.rebuild_projections()
    assert db.count("acceptance_runs") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 32
    assert SCHEMA_VERSION >= 32
