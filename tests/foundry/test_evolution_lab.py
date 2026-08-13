"""V6-S4: reproducible evaluation laboratory.

Baseline and candidate subjects run against the SAME frozen
representative workload: registered inputs with content hashes,
hash-verified copies per run, deterministic seeds where possible,
explicit nondeterminism handling, complete case coverage (no
cherry-picking), comparable output artifacts, and fail-closed
comparison against the improvement's frozen metrics (MMI) and
guardrails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.lab import (
    LabError,
    compare_evaluations,
    register_workload,
    run_evaluation,
    workload_by_id,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

PY = sys.executable


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _workload_dir(tmp_path: Path, count: int = 5) -> Path:
    directory = tmp_path / "workload"
    directory.mkdir(exist_ok=True)
    for index in range(count):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    return directory


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


def _echo_command() -> list[str]:
    return [PY, "-c",
            "import pathlib,sys\n"
            "case = pathlib.Path(sys.argv[1])\n"
            "print(case.read_text())\n"]


def _fast_command() -> list[str]:
    return [PY, "-c",
            "import pathlib,sys\n"
            "case = pathlib.Path(sys.argv[1])\n"
            "case.read_text()\n"]


# ---------------------------------------------------------------------------
# workload registration
# ---------------------------------------------------------------------------

def test_workload_registration_deterministic_and_idempotent(
        db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(
        db, name="w1", input_dir=directory,
        config={"mode": "echo"}, seeds=[1, 2, 3], controller="v6-s4")
    assert workload_id.startswith("workload-")
    again = register_workload(db, name="w1", input_dir=directory,
                              config={"mode": "echo"}, seeds=[1, 2, 3],
                              controller="v6-s4")
    assert again == workload_id
    events = [e for e in db.events()
              if e.event_type == "evaluation.workload_registered"]
    assert len(events) == 1
    workload = workload_by_id(db, workload_id)
    assert workload["config"] == {"mode": "echo"}
    assert workload["seeds"] == [1, 2, 3]
    assert len(workload["input_hashes"]) == 5


def test_workload_registration_secret_config_refused(
        db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    with pytest.raises(LabError):
        register_workload(db, name="w1", input_dir=directory,
                          config={"token": "sk-live-1234"},
                          seeds=[], controller="v6-s4")
    assert len(db.events()) == 0


def test_unknown_workload_refused(db: FoundryDatabase) -> None:
    with pytest.raises(LabError):
        run_evaluation(db, workload_id="workload-nope",
                       subject_kind="baseline", subject_identity="base-v1",
                       command=_echo_command(), controller="v6-s4")


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def test_baseline_run_all_cases(db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path, count=5)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    run_id = run_evaluation(db, workload_id=workload_id,
                            subject_kind="baseline",
                            subject_identity="base-v1",
                            command=_echo_command(), controller="v6-s4")
    row = db.row("evaluation_runs", "run_id", run_id)
    assert row is not None
    assert row["subject_kind"] == "baseline"
    assert row["exit_code"] == 0
    assert row["error_rate"] == 0.0
    cases = json.loads(row["case_results_json"])
    assert len(cases) == 5  # every case ran — no cherry-picking
    assert all(case["exit_code"] == 0 for case in cases)
    types = {e.event_type for e in db.events()}
    assert "evaluation.run_started" in types
    assert "evaluation.run_completed" in types


def test_candidate_run_same_input_hash(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    run_evaluation(db, workload_id=workload_id, subject_kind="baseline",
                   subject_identity="base-v1", command=_echo_command(),
                   controller="v6-s4")
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-v1",
                                  command=_fast_command(),
                                  controller="v6-s4")
    row = db.row("evaluation_runs", "run_id", candidate_id)
    baseline = db.all("evaluation_runs",
                      subject_kind="baseline")[0]
    assert row["input_hash"] == baseline["input_hash"]


def test_failing_case_records_error_rate(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path, count=4)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    broken = [PY, "-c",
              "import pathlib,sys\n"
              "case = pathlib.Path(sys.argv[1])\n"
              "if 'case-2' in case.name: raise SystemExit(7)\n"]
    run_id = run_evaluation(db, workload_id=workload_id,
                            subject_kind="candidate",
                            subject_identity="broken-v1",
                            command=broken, controller="v6-s4")
    row = db.row("evaluation_runs", "run_id", run_id)
    assert row is not None
    assert row["exit_code"] == 1  # overall failure
    assert row["error_rate"] == 0.25  # 1 of 4 cases failed


def test_seeded_determinism(db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[42],
                                    controller="v6-s4")
    first = run_evaluation(db, workload_id=workload_id,
                           subject_kind="baseline",
                           subject_identity="base-v1",
                           command=_echo_command(), controller="v6-s4")
    second = run_evaluation(db, workload_id=workload_id,
                            subject_kind="baseline",
                            subject_identity="base-v1",
                            command=_echo_command(), controller="v6-s4")
    row_a = db.row("evaluation_runs", "run_id", first)
    row_b = db.row("evaluation_runs", "run_id", second)
    assert row_a["output_hashes_json"] == row_b["output_hashes_json"]


def test_nondeterminism_handling_recorded(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(
        db, name="w1", input_dir=directory,
        config={"nondeterministic": True,
                "treatment": "explicit variance recording"},
        seeds=[], controller="v6-s4")
    run_id = run_evaluation(db, workload_id=workload_id,
                            subject_kind="candidate",
                            subject_identity="cand-v1",
                            command=_echo_command(), controller="v6-s4")
    row = db.row("evaluation_runs", "run_id", run_id)
    assert row is not None
    assert "explicit variance recording" in (
        row["nondeterminism_handling"] or "")


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def _improvement_mmi(db: FoundryDatabase, minimum_delta: float,
                     direction: str = "decrease") -> str:
    return record_improvement(
        db,
        problem="latency exceeds the threshold on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="A faster subject reduces wall-clock runtime",
        expected_mechanism="less per-case work",
        baseline_metric={"name": "suite_runtime_s", "value": 1.0,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": minimum_delta, "direction": direction,
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


def test_compare_improvement_met(db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    slow = [PY, "-c",
            "import pathlib,sys,time\n"
            "pathlib.Path(sys.argv[1]).read_text()\n"
            "time.sleep(0.05)\n"]
    fast = [PY, "-c",
            "import pathlib,sys\n"
            "pathlib.Path(sys.argv[1]).read_text()\n"]
    # MMI 0.01s decrease: the ~0.25s total delta must satisfy it
    improvement_id = _improvement_mmi(db, 0.01)
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-v1",
                                 command=slow, controller="v6-s4")
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-v1",
                                  command=fast, controller="v6-s4")
    result = compare_evaluations(db, baseline_run_id=baseline_id,
                                 candidate_run_id=candidate_id,
                                 improvement_id=improvement_id)
    assert result["comparable"] is True
    assert result["improvement_met"] is True
    assert result["guardrails_violated"] == []
    assert result["metric_key"] == "duration_s"
    assert result["delta"] < 0


def test_compare_improvement_not_met(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    same = _echo_command()
    improvement_id = _improvement(db)
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-v1",
                                 command=same, controller="v6-s4")
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-v1",
                                  command=same, controller="v6-s4")
    result = compare_evaluations(db, baseline_run_id=baseline_id,
                                 candidate_run_id=candidate_id,
                                 improvement_id=improvement_id)
    assert result["improvement_met"] is False  # |delta| < 30s MMI


def test_compare_guardrail_violation(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    broken = [PY, "-c",
              "import pathlib,sys\n"
              "pathlib.Path(sys.argv[1]).read_text()\n"
              "raise SystemExit(3)\n"]
    improvement_id = _improvement(db)
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-v1",
                                 command=_echo_command(),
                                 controller="v6-s4")
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="broken-v1",
                                  command=broken, controller="v6-s4")
    result = compare_evaluations(db, baseline_run_id=baseline_id,
                                 candidate_run_id=candidate_id,
                                 improvement_id=improvement_id)
    assert "correctness" in result["guardrails_violated"]


def test_compare_refusals(db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    other_dir = tmp_path / "other-workload"
    other_dir.mkdir()
    (other_dir / "x.txt").write_text("x")
    other_workload = register_workload(db, name="w2", input_dir=other_dir,
                                       config={}, seeds=[],
                                       controller="v6-s4")
    improvement_id = _improvement(db)
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-v1",
                                 command=_echo_command(),
                                 controller="v6-s4")
    other_run = run_evaluation(db, workload_id=other_workload,
                               subject_kind="candidate",
                               subject_identity="cand-other",
                               command=_echo_command(), controller="v6-s4")
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-v1",
                                  command=_echo_command(),
                                  controller="v6-s4")
    with pytest.raises(LabError):
        compare_evaluations(db, baseline_run_id="run-nope",
                            candidate_run_id=candidate_id,
                            improvement_id=improvement_id)
    with pytest.raises(LabError):
        compare_evaluations(db, baseline_run_id=candidate_id,
                            candidate_run_id=baseline_id,
                            improvement_id=improvement_id)
    with pytest.raises(LabError):
        compare_evaluations(db, baseline_run_id=baseline_id,
                            candidate_run_id=other_run,
                            improvement_id=improvement_id)
    with pytest.raises(LabError):
        compare_evaluations(db, baseline_run_id=baseline_id,
                            candidate_run_id=candidate_id,
                            improvement_id="improvement-nope")
    result = compare_evaluations(db, baseline_run_id=baseline_id,
                                 candidate_run_id=candidate_id,
                                 improvement_id=improvement_id)
    assert result["comparable"] is True


def test_bad_subject_kind_and_command_refused(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    with pytest.raises(LabError):
        run_evaluation(db, workload_id=workload_id,
                       subject_kind="alien", subject_identity="x",
                       command=_echo_command(), controller="v6-s4")
    with pytest.raises(LabError):
        run_evaluation(db, workload_id=workload_id,
                       subject_kind="baseline", subject_identity="x",
                       command=[], controller="v6-s4")


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase, tmp_path: Path) -> None:
    directory = _workload_dir(tmp_path)
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller="v6-s4")
    run_evaluation(db, workload_id=workload_id, subject_kind="baseline",
                   subject_identity="base-v1", command=_echo_command(),
                   controller="v6-s4")
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("evaluation_runs")
    db.rebuild_projections()
    assert db.count("evaluation_runs") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 24
    assert SCHEMA_VERSION >= 24
