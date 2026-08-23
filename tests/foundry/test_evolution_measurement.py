"""V6 lane: baseline + counterfactual measurement layer.

Covers: snapshot capture (MEASURED + INSUFFICIENT_SAMPLE honesty),
dedupe of identical re-captures, refusal for unknown improvements,
typed UNKNOWN comparisons (unequal spans, metric mismatch, wrong
subject kinds, insufficient samples), a full MEASURED comparison with
confounder drift, and post-comparison reconcile consistency.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.measurement import (
    MIN_EVAL_SAMPLE,
    MeasurementError,
    capture_measurement,
    compare_measurements,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
BASE = datetime.fromisoformat("2026-08-22T12:00:00")


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _at(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat()


def _backdated(db: FoundryDatabase, event_type: str, payload: dict,
               idem_key: str, at: str) -> None:
    event = Event.create(event_type, payload, source_type="test",
                         source_id="measurement", idempotency_key=idem_key)
    db.append(replace(event, recorded_at=at))


def _seed_eval(db: FoundryDatabase, run_id: str, workload_id: str,
               subject_kind: str, at: str, *, duration_s: float = 1.0,
               error_rate: float = 0.0, cost_usd: float = 0.0) -> None:
    _backdated(db, "evaluation.run_started",
               {"run_id": run_id, "workload_id": workload_id,
                "subject_kind": subject_kind, "subject_identity": "x",
                "command_hash": "h"}, f"ers:{run_id}", at)
    _backdated(db, "evaluation.run_completed",
               {"run_id": run_id, "workload_id": workload_id,
                "subject_kind": subject_kind, "subject_identity": "x",
                "command_hash": "h", "input_hash": "ih", "exit_code":
                    0 if error_rate == 0 else 1,
                "duration_s": duration_s, "error_rate": error_rate,
                "cost_usd": cost_usd, "case_results": [],
                "output_hashes": {},
                "nondeterminism_handling": "not_seeded"},
               f"erc:{run_id}", at)


HYPOTHESIS_KWARGS = dict(
    problem="candidate evaluations are slower than baseline",
    evidence_refs=["evaluation_runs/seed"],
    provenance={"source": "test"},
    hypothesis="reduce candidate evaluation latency",
    expected_mechanism="removing a redundant pass shortens each case",
    baseline_metric={"name": "duration_s", "value": 4.0, "unit": "s",
                     "source": "evaluation_runs/seed"},
    mmi={"minimum_delta": 0.5, "direction": "decrease", "unit": "s"},
    guardrails={"correctness": "no new failures", "safety":
                "no live mutation", "cost": "no paid calls",
                "latency": "p95 not worse"},
    workload={"ref": "w1", "hash": "a" * 64},
    observation={"requirement": "1 cycle", "cycles": 1},
    declared_risk="R2",
    surfaces=["execution/"],
    budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
    reversibility={"plan": "git revert", "rollback_threshold": "5m"},
)


@pytest.fixture()
def improvement_id(db: FoundryDatabase) -> str:
    return record_improvement(db, **HYPOTHESIS_KWARGS)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def test_capture_requires_known_improvement(db: FoundryDatabase) -> None:
    with pytest.raises(MeasurementError):
        capture_measurement(db, improvement_id="improvement-nope",
                            subject_kind="baseline",
                            window_start=_at(0), window_end=_at(24))


def test_capture_insufficient_sample_is_explicit(
        db: FoundryDatabase, improvement_id: str) -> None:
    _seed_eval(db, "m1", "w1", "baseline", _at(1), duration_s=4.0)
    payload = capture_measurement(
        db, improvement_id=improvement_id, subject_kind="baseline",
        window_start=_at(0), window_end=_at(24))
    assert payload["status"] == "INSUFFICIENT_SAMPLE"
    assert payload["sample_size"] == 1 < MIN_EVAL_SAMPLE
    assert payload["latency_mean_s"] is not None  # observed, not compared


def test_capture_measured_with_distribution_and_confounders(
        db: FoundryDatabase, improvement_id: str) -> None:
    _seed_eval(db, "b1", "w1", "baseline", _at(1), duration_s=3.0)
    _seed_eval(db, "b2", "w1", "baseline", _at(2), duration_s=5.0,
               cost_usd=0.25)
    payload = capture_measurement(
        db, improvement_id=improvement_id, subject_kind="baseline",
        window_start=_at(0), window_end=_at(24))
    assert payload["status"] == "MEASURED"
    assert payload["sample_size"] == 2
    assert payload["distribution_durations_s"] == [3.0, 5.0]
    assert payload["latency_mean_s"] == 4.0
    assert payload["latency_p95_s"] == 5.0
    assert payload["cost_usd_total"] == 0.25
    assert payload["confounders"]["workload_ids"] == ["w1"]
    assert payload["target_metric"]["name"] == "duration_s"
    assert payload["evidence_refs"][0] == "evaluation_runs/b1"


def test_identical_recapture_dedupes(db: FoundryDatabase,
                                     improvement_id: str) -> None:
    _seed_eval(db, "d1", "w1", "baseline", _at(1), duration_s=3.0)
    _seed_eval(db, "d2", "w1", "baseline", _at(2), duration_s=5.0)
    one = capture_measurement(db, improvement_id=improvement_id,
                              subject_kind="baseline",
                              window_start=_at(0), window_end=_at(24))
    two = capture_measurement(db, improvement_id=improvement_id,
                              subject_kind="baseline",
                              window_start=_at(0), window_end=_at(24))
    assert one["measurement_id"] == two["measurement_id"]


# ---------------------------------------------------------------------------
# comparison — typed UNKNOWN reasons, never silent incompatible compares
# ---------------------------------------------------------------------------

def test_compare_unequal_spans_unknown(db: FoundryDatabase,
                                       improvement_id: str) -> None:
    _seed_eval(db, "u1", "w1", "baseline", _at(1), duration_s=4.0)
    _seed_eval(db, "u2", "w1", "baseline", _at(2), duration_s=4.0)
    before = capture_measurement(db, improvement_id=improvement_id,
                                 subject_kind="baseline",
                                 window_start=_at(0), window_end=_at(24))
    _seed_eval(db, "uc1", "w1", "candidate", _at(25), duration_s=4.0)
    _seed_eval(db, "uc2", "w1", "candidate", _at(26), duration_s=4.0)
    after = capture_measurement(db, improvement_id=improvement_id,
                                subject_kind="candidate",
                                window_start=_at(24), window_end=_at(72))
    result = compare_measurements(db, before_id=before["measurement_id"],
                                  after_id=after["measurement_id"])
    assert result["comparison"] == "UNKNOWN"
    assert "unequal spans" in result["reason"]


def test_compare_metric_mismatch_unknown(db: FoundryDatabase,
                                         improvement_id: str) -> None:
    other = dict(HYPOTHESIS_KWARGS)
    other["baseline_metric"] = {**HYPOTHESIS_KWARGS["baseline_metric"],
                                "name": "error_rate"}
    other_id = record_improvement(db, **other)
    _seed_eval(db, "x1", "w1", "baseline", _at(1), duration_s=4.0)
    _seed_eval(db, "x2", "w1", "baseline", _at(2), duration_s=4.0)
    before = capture_measurement(db, improvement_id=improvement_id,
                                 subject_kind="baseline",
                                 window_start=_at(0), window_end=_at(24))
    _seed_eval(db, "xc1", "w1", "candidate", _at(25), duration_s=4.0)
    _seed_eval(db, "xc2", "w1", "candidate", _at(26), duration_s=4.0)
    after = capture_measurement(db, improvement_id=other_id,
                                subject_kind="candidate",
                                window_start=_at(24), window_end=_at(48))
    result = compare_measurements(db, before_id=before["measurement_id"],
                                  after_id=after["measurement_id"])
    assert result["comparison"] == "UNKNOWN"
    assert "incompatible metric identities" in result["reason"]


def test_compare_wrong_subject_kinds_unknown(db: FoundryDatabase,
                                             improvement_id: str) -> None:
    _seed_eval(db, "k1", "w1", "baseline", _at(1), duration_s=4.0)
    _seed_eval(db, "k2", "w1", "baseline", _at(2), duration_s=4.0)
    first = capture_measurement(db, improvement_id=improvement_id,
                                subject_kind="baseline",
                                window_start=_at(0), window_end=_at(24))
    second = capture_measurement(db, improvement_id=improvement_id,
                                 subject_kind="baseline",
                                 window_start=_at(24), window_end=_at(48))
    result = compare_measurements(db, before_id=first["measurement_id"],
                                  after_id=second["measurement_id"])
    assert result["comparison"] == "UNKNOWN"
    assert "not a candidate snapshot" in result["reason"]


def test_compare_measured_delta_and_drift(db: FoundryDatabase,
                                          improvement_id: str) -> None:
    _seed_eval(db, "g1", "w1", "baseline", _at(1), duration_s=4.0,
               cost_usd=0.10)
    _seed_eval(db, "g2", "w1", "baseline", _at(2), duration_s=4.0,
               cost_usd=0.10)
    before = capture_measurement(db, improvement_id=improvement_id,
                                 subject_kind="baseline",
                                 window_start=_at(0), window_end=_at(24))
    _seed_eval(db, "gc1", "w1", "candidate", _at(25), duration_s=3.0,
               cost_usd=0.05)
    _seed_eval(db, "gc2", "w1", "candidate", _at(26), duration_s=3.0,
               cost_usd=0.05)
    after = capture_measurement(db, improvement_id=improvement_id,
                                subject_kind="candidate",
                                window_start=_at(24), window_end=_at(48))
    result = compare_measurements(db, before_id=before["measurement_id"],
                                  after_id=after["measurement_id"])
    assert result["comparison"] == "MEASURED"
    assert result["delta_latency_s"] == -1.0
    assert result["delta_cost_usd"] == pytest.approx(-0.1)
    assert result["confounder_drift"]["workload_ids"]["before"] == \
        result["confounder_drift"]["workload_ids"]["after"]
    report = reconcile(db)
    assert report.consistent


def test_compare_refuses_missing_measurements(db: FoundryDatabase) -> None:
    with pytest.raises(MeasurementError):
        compare_measurements(db, before_id="measurement-nope",
                             after_id="measurement-nope")
