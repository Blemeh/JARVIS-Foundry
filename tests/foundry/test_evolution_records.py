"""V6-S1: hypothesis, metric, and risk-class contract.

Durable, fail-closed improvement records: a measured problem, evidence
and provenance, a frozen hypothesis with expected mechanism, baseline
metric with a minimum meaningful improvement, correctness/safety/cost/
latency guardrails, evaluation workload, observation requirement, a
deterministic risk class (declared OR surface-derived, whichever is
higher; unknown fails closed to R3), budget, capability scope,
reversibility and a rollback threshold. The record freezes BEFORE any
candidate implementation; missing or mutable metrics make a candidate
promotion-ineligible. A builder may propose a risk class but can never
lower it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import (
    ImprovementError,
    classify_risk,
    improvement_eligibility,
    raise_improvement_risk,
    record_improvement,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _complete(**overrides: object) -> dict[str, object]:
    """A complete, valid improvement-record payload (frozen contract)."""
    payload: dict[str, object] = {
        "problem": "suite runtime exceeds the 5-minute budget on this host",
        "evidence_refs": ["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        "provenance": {"source": "v6-s0 baseline measurement",
                       "observed_at": "2026-08-13T15:00:00Z"},
        "hypothesis": ("Splitting the fault-injection tests into a "
                       "separate pytest session reduces wall-clock runtime"),
        "expected_mechanism": ("parallel session isolation removes the "
                               "serialized daemon-spawn waits"),
        "baseline_metric": {"name": "suite_runtime_s",
                            "value": 313.92, "unit": "seconds",
                            "source": "suite-v6-s0-baseline.txt"},
        "mmi": {"minimum_delta": 30.0, "direction": "decrease",
                "unit": "seconds"},
        "guardrails": {"correctness": "full suite must stay green "
                                       "(719 tests, EXIT=0)",
                       "safety": "no live DB, daemon or vault mutation",
                       "cost": "no paid provider calls",
                       "latency": "no test latency regression"},
        "workload": {"ref": "tests/foundry",
                     "hash": "2bbeaea3985f12a2d8470059721e342f010e3534a40f5cd3abce7313a723b8bf"},
        "observation": {"requirement": "3 full-suite runs post-promotion",
                        "cycles": 3},
        "declared_risk": "R2",
        "surfaces": ["tests/foundry", "pyproject.toml"],
        "budget": {"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        "reversibility": {"plan": "git revert the candidate commit",
                          "rollback_threshold": "suite runtime above 5m"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# recording and freezing
# ---------------------------------------------------------------------------

def test_complete_record_is_frozen_and_eligible(db: FoundryDatabase) -> None:
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    assert improvement_id.startswith("improvement-")
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None
    assert row["state"] == "FROZEN"
    assert row["risk_class"] == "R2"
    eligible, reason = improvement_eligibility(db, improvement_id)
    assert eligible is True
    assert reason == "eligible"


def test_missing_baseline_metric_is_refused(db: FoundryDatabase) -> None:
    with pytest.raises(ImprovementError):
        record_improvement(db,  # type: ignore[arg-type]
                           **_complete(baseline_metric=None))
    assert db.count("improvement_records") == 0
    assert len(db.events()) == 0


def test_missing_guardrail_is_refused(db: FoundryDatabase) -> None:
    payload = _complete()
    guardrails = dict(payload["guardrails"])  # type: ignore[arg-type]
    del guardrails["safety"]
    payload["guardrails"] = guardrails
    with pytest.raises(ImprovementError):
        record_improvement(db, **payload)  # type: ignore[arg-type]
    assert db.count("improvement_records") == 0


def test_nonfinite_metric_value_is_refused(db: FoundryDatabase) -> None:
    metric = dict(_complete()["baseline_metric"])  # type: ignore[arg-type]
    metric["value"] = float("nan")
    with pytest.raises(ImprovementError):
        record_improvement(db, **_complete(baseline_metric=metric))  # type: ignore[arg-type]
    assert db.count("improvement_records") == 0


def test_bad_workload_hash_is_refused(db: FoundryDatabase) -> None:
    workload = dict(_complete()["workload"])  # type: ignore[arg-type]
    workload["hash"] = "not-a-hash"
    with pytest.raises(ImprovementError):
        record_improvement(db, **_complete(workload=workload))  # type: ignore[arg-type]


def test_identical_content_dedups(db: FoundryDatabase) -> None:
    first = record_improvement(db, **_complete())  # type: ignore[arg-type]
    second = record_improvement(db, **_complete())  # type: ignore[arg-type]
    assert first == second
    assert db.count("improvement_records") == 1
    events = [e for e in db.events()
              if e.event_type == "improvement.recorded"]
    assert len(events) == 1


def test_changed_content_creates_new_record_and_supersedes(
        db: FoundryDatabase) -> None:
    first = record_improvement(db, **_complete())  # type: ignore[arg-type]
    second = record_improvement(db,  # type: ignore[arg-type]
                                **_complete(
                                    hypothesis="a different hypothesis"))
    assert second != first
    row = db.row("improvement_records", "improvement_id", second)
    assert row is not None
    assert row["supersedes"] is None
    third = record_improvement(db, supersedes=first,  # type: ignore[arg-type]
                               **_complete(
                                   hypothesis="a third hypothesis"))
    row = db.row("improvement_records", "improvement_id", third)
    assert row is not None
    assert row["supersedes"] == first
    old = db.row("improvement_records", "improvement_id", first)
    assert old is not None and old["state"] == "FROZEN"  # history preserved


def test_supersedes_unknown_parent_refused(db: FoundryDatabase) -> None:
    with pytest.raises(ImprovementError):
        record_improvement(db, supersedes="improvement-nope",  # type: ignore[arg-type]
                           **_complete())


def test_immutable_after_record(db: FoundryDatabase) -> None:
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None
    # risk raise touches ONLY the risk class
    raise_improvement_risk(db, improvement_id, "R3",
                           reason="new control-surface finding")
    after = db.row("improvement_records", "improvement_id", improvement_id)
    assert after is not None
    assert after["risk_class"] == "R3"
    assert after["hypothesis"] == row["hypothesis"]
    assert after["baseline_metric_json"] == row["baseline_metric_json"]


# ---------------------------------------------------------------------------
# deterministic risk classification
# ---------------------------------------------------------------------------

def test_declared_r1_with_control_surface_forces_r3(
        db: FoundryDatabase) -> None:
    improvement_id = record_improvement(  # type: ignore[arg-type]
        db, **_complete(declared_risk="R1",
                        surfaces=["src/jarvis_foundry/control/broker.py"]))
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None
    assert row["risk_class"] == "R3"
    assert row["risk_class_declared"] == "R1"


def test_unknown_surface_fails_closed_to_r3(db: FoundryDatabase) -> None:
    assert classify_risk(["some/unknown/surface.py"]) == "R3"
    improvement_id = record_improvement(  # type: ignore[arg-type]
        db, **_complete(declared_risk="R0",
                        surfaces=["some/unknown/surface.py"]))
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None and row["risk_class"] == "R3"


def test_known_surface_mapping(db: FoundryDatabase) -> None:
    assert classify_risk(["docs/planning/V6_SLICE_MANIFEST.md"]) == "R0"
    assert classify_risk(["skills/operator/steering.md"]) == "R1"
    assert classify_risk(["src/jarvis_foundry/mission/loop.py"]) == "R2"
    assert classify_risk(["src/jarvis_foundry/migrations.py"]) == "R3"
    assert classify_risk(["src/jarvis_foundry/supervisor/estop.py"]) == "R3"
    assert classify_risk(["src/jarvis_foundry/control/capabilities.py"]) == "R3"
    assert classify_risk(["src/jarvis_foundry/events.py"]) == "R3"
    assert classify_risk([]) == "R0"  # no surfaces: nothing changes


def test_risk_lower_attempt_refused(db: FoundryDatabase) -> None:
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    with pytest.raises(ImprovementError):
        raise_improvement_risk(db, improvement_id, "R1", reason="nope")
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None and row["risk_class"] == "R2"
    # same-class re-raise is also refused (must be strictly higher)
    with pytest.raises(ImprovementError):
        raise_improvement_risk(db, improvement_id, "R2", reason="same")
    assert len(db.events()) == 1  # only the recorded event


def test_risk_raise_requires_reason_and_existing_record(
        db: FoundryDatabase) -> None:
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    with pytest.raises(ImprovementError):
        raise_improvement_risk(db, improvement_id, "R3", reason="")
    with pytest.raises(ImprovementError):
        raise_improvement_risk(db, "improvement-nope", "R3",
                               reason="missing row")
    row = db.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None and row["risk_class"] == "R2"


# ---------------------------------------------------------------------------
# boundary guards
# ---------------------------------------------------------------------------

def test_secret_like_content_rejected(db: FoundryDatabase) -> None:
    with pytest.raises(ImprovementError):
        record_improvement(  # type: ignore[arg-type]
            db, **_complete(problem="leak api_key=sk-live-1234 in the log"))
    assert len(db.events()) == 0
    with db.connect() as connection:
        hits = connection.execute(
            "SELECT COUNT(*) FROM events WHERE payload_json LIKE "
            "'%sk-live-1234%'").fetchone()[0]
    assert hits == 0


def test_eligibility_unknown_and_incomplete(db: FoundryDatabase) -> None:
    eligible, reason = improvement_eligibility(db, "improvement-nope")
    assert eligible is False and "unknown" in reason
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    # a record missing its guardrails can never exist, but eligibility
    # must still fail closed on any absent metric field
    with db.connect() as connection:
        connection.execute(
            "UPDATE improvement_records SET guardrails_json = '{}' "
            "WHERE improvement_id = ?", (improvement_id,))
    eligible, reason = improvement_eligibility(db, improvement_id)
    assert eligible is False and "guardrails" in reason


# ---------------------------------------------------------------------------
# projection / reconciliation integration
# ---------------------------------------------------------------------------

def test_projection_touch_points(db: FoundryDatabase) -> None:
    improvement_id = record_improvement(db, **_complete())  # type: ignore[arg-type]
    assert db.all("improvement_records")[0]["improvement_id"] == \
        improvement_id
    report = reconcile(db)
    assert report.consistent is True
    assert report.mismatches == [] and report.unexpected_rows == [] \
        and report.missing_rows == []
    before = db.count("improvement_records")
    rebuilt = db.rebuild_projections()
    assert db.count("improvement_records") == before
    assert rebuilt >= 0


def test_schema_version_pinned_to_constant(db: FoundryDatabase) -> None:
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # never a literal
    assert SCHEMA_VERSION >= 21  # the v21 improvement_records migration
