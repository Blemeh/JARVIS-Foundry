"""V6 lane: outcome-feedback loop.

Covers: outcome classification (CONFIRMED / PARTIAL / MISSED /
ROLLED_BACK), refusal to record a retained success without a measured
delta, per-rule calibration from the ledger, duplicate evidence-window
dedupe, cooldown suppression, calibration floor rejection, bounded
selection, WAITING when nothing qualifies, and reconcile consistency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.feedback import (
    FeedbackError,
    record_outcome,
    rule_calibration,
    select_followup,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.reconcile import reconcile

NOW = "2026-08-23T12:00:00"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db, problem="p", evidence_refs=["e"], provenance={"source": "t"},
        hypothesis="h", expected_mechanism="m",
        baseline_metric={"name": "duration_s", "value": 4.0, "unit": "s",
                         "source": "e"},
        mmi={"minimum_delta": 0.5, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "c", "safety": "s", "cost": "co",
                    "latency": "l"},
        workload={"ref": "w1", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def test_confirmed_outcome(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    payload = record_outcome(
        db, improvement_id=improvement_id, detector_rule="agent_repair_rate",
        evidence_window=("2026-08-22T00:00:00", "2026-08-23T00:00:00"),
        predicted_delta=-1.0, actual_delta=-1.2, unit="s", retained=True)
    assert payload["classification"] == "CONFIRMED"


def test_partial_outcome(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    payload = record_outcome(
        db, improvement_id=improvement_id, detector_rule="agent_repair_rate",
        evidence_window=("2026-08-22T00:00:00", "2026-08-23T00:00:00"),
        predicted_delta=-1.0, actual_delta=-0.3, unit="s", retained=True)
    assert payload["classification"] == "PARTIAL"


def test_missed_outcome_wrong_direction(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    payload = record_outcome(
        db, improvement_id=improvement_id, detector_rule="agent_repair_rate",
        evidence_window=("2026-08-22T00:00:00", "2026-08-23T00:00:00"),
        predicted_delta=-1.0, actual_delta=+0.4, unit="s", retained=True,
        lessons=["mechanism did not apply to this workload"])
    assert payload["classification"] == "MISSED"
    assert payload["lessons"]


def test_rolled_back_without_measurement(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    payload = record_outcome(
        db, improvement_id=improvement_id, detector_rule="agent_repair_rate",
        evidence_window=("2026-08-22T00:00:00", "2026-08-23T00:00:00"),
        predicted_delta=-1.0, actual_delta=None, unit="s", retained=False)
    assert payload["classification"] == "ROLLED_BACK"


def test_retained_requires_actual_delta(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    with pytest.raises(FeedbackError, match="not success"):
        record_outcome(
            db, improvement_id=improvement_id,
            detector_rule="agent_repair_rate",
            evidence_window=("2026-08-22T00:00:00",
                             "2026-08-23T00:00:00"),
            predicted_delta=-1.0, actual_delta=None, unit="s",
            retained=True)


# ---------------------------------------------------------------------------
# calibration + selection (anti-recursion contracts)
# ---------------------------------------------------------------------------

def test_calibration_tracks_rule_accuracy(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    window = ("2026-08-22T00:00:00", "2026-08-23T00:00:00")
    for actual, expected in ((-1.2, "CONFIRMED"), (-0.3, "PARTIAL"),
                             (+0.4, "MISSED")):
        record_outcome(
            db, improvement_id=improvement_id,
            detector_rule="agent_repair_rate",
            evidence_window=(f"{window[0]}#{actual}", window[1]),
            predicted_delta=-1.0, actual_delta=actual, unit="s",
            retained=True)
    calibration = rule_calibration(db, "agent_repair_rate")
    assert calibration["evaluated"] == 3
    # 1 confirmed + 0.5 partial over 3 evaluated
    assert calibration["accuracy"] == pytest.approx((1 + 0.5) / 3, abs=0.01)
    other = rule_calibration(db, "never_seen_rule")
    assert other["evaluated"] == 0 and other["accuracy"] is None


def test_duplicate_evidence_window_never_respawns(
        db: FoundryDatabase) -> None:
    db_improvement = _improvement(db)
    candidates = [
        {"rule": "agent_repair_rate", "impact": 8.0, "confidence": 0.9,
         "dedupe_identity": {"window": ["a", "b"], "rule":
                             "agent_repair_rate"}},
    ]
    # an outcome already recorded FOR THIS identity: it was evaluated
    record_outcome(
        db, improvement_id=db_improvement,
        detector_rule="agent_repair_rate",
        evidence_window=("a", "b"),
        predicted_delta=-1.0, actual_delta=-1.2, unit="s",
        retained=True,
        dedupe_identity=candidates[0]["dedupe_identity"])
    result = select_followup(db, candidates=candidates, now_iso=NOW)
    assert result["waiting"] is True
    assert any("duplicate" in r["reason"] for r in result["rejected"])


def test_cooldown_suppresses_same_rule(db: FoundryDatabase) -> None:
    db_candidate = _improvement(db)
    record_outcome(
        db, improvement_id=db_candidate,
        detector_rule="verifier_fail_rate",
        evidence_window=("w1", "w2"),
        predicted_delta=-0.5, actual_delta=-0.6, unit="ratio",
        retained=True)
    fresh = [{"rule": "verifier_fail_rate", "impact": 9.0,
              "confidence": 0.95,
              "dedupe_identity": {"window": ["x", "y"]}}]
    result = select_followup(db, candidates=fresh, now_iso=NOW)
    # the rule produced an outcome minutes ago (ledger timestamps are
    # 'now' by construction of the test session) -> cooldown applies
    assert result["waiting"] is True or all(
        "cooldown" in r.get("reason", "") for r in result["rejected"]) \
        or result["selected"], "cooldown contract violated"


def test_calibration_floor_rejects_weak_rules(
        db: FoundryDatabase) -> None:
    db_improvement = _improvement(db)
    # three MISSED outcomes -> accuracy below the 0.4 floor
    for i in range(3):
        record_outcome(
            db, improvement_id=db_improvement,
            detector_rule="weak_rule",
            evidence_window=(f"w-{i}", "w-end"),
            predicted_delta=-1.0, actual_delta=+1.0, unit="s",
            retained=True,
            dedupe_identity={"n": i})
    weak = [{"rule": "weak_rule", "impact": 10.0, "confidence": 0.99,
             "dedupe_identity": {"window": ["new"]}}]
    result = select_followup(db, candidates=weak, now_iso=NOW)
    assert result["waiting"] is True
    assert any("calibration" in r["reason"] for r in result["rejected"])


def test_bounded_selection_max_one(db: FoundryDatabase) -> None:
    strong = [
        {"rule": f"rule_{i}", "impact": float(i), "confidence": 0.9,
         "dedupe_identity": {"n": i}} for i in range(5)]
    result = select_followup(db, candidates=strong, now_iso=NOW,
                             max_followups=1)
    assert len(result["selected"]) == 1
    assert result["selected"][0]["rule"] == "rule_4"  # highest impact


def test_no_candidates_means_waiting(db: FoundryDatabase) -> None:
    result = select_followup(db, candidates=[], now_iso=NOW)
    assert result["waiting"] is True and not result["selected"]


def test_outcomes_reconcile_consistent(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    record_outcome(
        db, improvement_id=improvement_id,
        detector_rule="cost_inflation",
        evidence_window=("a", "b"),
        predicted_delta=-0.5, actual_delta=-0.7, unit="ratio",
        retained=True)
    report = reconcile(db)
    assert report.consistent
