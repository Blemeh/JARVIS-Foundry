"""V3 WP1 tests: schema v35, projection handlers, rebuild reconcile."""
from __future__ import annotations

import sqlite3

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.projections import PROJECTION_TABLES
from jarvis_foundry.reconcile import inspect as reconcile_inspect

from v3_fixtures import V3_SCHEMA_VERSION


def _fresh_db(tmp_path, name="db.sqlite"):
    database = FoundryDatabase(tmp_path / name)
    database.initialize()
    return database


def test_schema_version_is_v3_and_projection_registry_complete():
    # V3 owned v36; later additive migrations (Lane 4 software-intelligence
    # v37) may raise the current version without changing V3's own tables.
    assert SCHEMA_VERSION >= V3_SCHEMA_VERSION
    for table in ("candidate_outcomes", "scoring_policy_registry",
                  "discovery_portfolio"):
        assert table in PROJECTION_TABLES


def test_new_candidate_columns_present_and_typed_payload_persisted(tmp_path):
    db = _fresh_db(tmp_path)
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": "cand-typed-1", "mission_id": "mission-x",
         "source": "maintenance", "objective": "Fix the failing nightly integration",
         "evidence": [{"kind": "test", "ref": "nightly"}],
         "duplicate_of": None,
         "fingerprint": "f" * 64,
         "candidate_type": "repair",
         "expected_outcome": "nightly suite green",
         "provenance_hash": "a" * 64,
         "expires_at": None},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="t:gen:typed"))
    with db.connect() as connection:
        row = connection.execute(
            "SELECT fingerprint, candidate_type, expected_outcome, "
            "provenance_hash, status FROM candidates WHERE candidate_id='cand-typed-1'"
        ).fetchone()
    assert tuple(row) == ("f" * 64, "repair", "nightly suite green", "a" * 64, "NEW")


def test_legacy_candidate_payload_still_replays(tmp_path):
    db = _fresh_db(tmp_path)
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": "cand-legacy-1", "mission_id": "mission-x",
         "source": "system", "objective": "Investigate unknown root cause of outage",
         "evidence": [], "duplicate_of": None},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="t:gen:legacy"))
    row = db.row("candidates", "candidate_id", "cand-legacy-1")
    assert row["status"] == "NEW"
    assert row["fingerprint"] is None


def test_policy_registered_projection_latest_version_wins(tmp_path):
    db = _fresh_db(tmp_path)
    db.append(Event.create(
        "scoring.policy_registered",
        {"policy_version": "p1", "weights": {"w": 1}, "thresholds": {},
         "hard_filters": {}},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="scoring.policy:p1"))
    policies = db.all("scoring_policy_registry")
    assert len(policies) == 1 and policies[0]["active"] == 1
    db.append(Event.create(
        "scoring.policy_registered",
        {"policy_version": "p2", "weights": {}, "thresholds": {},
         "hard_filters": {}},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="scoring.policy:p2"))
    active = [row for row in db.all("scoring_policy_registry") if row["active"] == 1]
    assert [row["policy_version"] for row in active] == ["p2"]


def test_policy_superseded_restores_previous_active(tmp_path):
    db = _fresh_db(tmp_path)
    for version in ("p1", "p2"):
        db.append(Event.create(
            "scoring.policy_registered",
            {"policy_version": version, "weights": {}, "thresholds": {},
             "hard_filters": {}},
            source_type="supervisor", source_id="v3-test",
            idempotency_key=f"scoring.policy:{version}"))
    db.append(Event.create(
        "scoring.policy_superseded",
        {"old_version": "p2", "new_version": "p1", "reason": "rollback"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="scoring.supersede:p2:p1"))
    rows = {row["policy_version"]: row["active"]
            for row in db.all("scoring_policy_registry")}
    assert rows == {"p1": 1, "p2": 0}


def test_portfolio_evaluated_and_deferred_requeued_states(tmp_path):
    db = _fresh_db(tmp_path)
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": "cand-port-1", "mission_id": "mission-x",
         "source": "system", "objective": "Fix outage risk now",
         "evidence": [], "duplicate_of": None},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="t:port:gen"))
    db.append(Event.create(
        "portfolio.candidate_deferred",
        {"candidate_id": "cand-port-1", "reason": "capacity",
         "cooldown_until": "2026-08-23T00:00:00Z"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="defer:cand-port-1:l1"))
    assert db.row("candidates", "candidate_id", "cand-port-1")["status"] == "DEFERRED"
    db.append(Event.create(
        "portfolio.evaluated",
        {"loop_id": "loop-1", "policy_version": "v3-policy-2026-08-21",
         "eligible_count": 0, "deferred_count": 1, "rejected_count": 0,
         "capacity_snapshot": {}},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="portfolio:loop-1:v3-policy-2026-08-21"))
    portfolio = db.row("discovery_portfolio", "loop_id", "loop-1")
    assert portfolio["deferred_count"] == 1
    db.append(Event.create(
        "portfolio.candidate_requeued",
        {"candidate_id": "cand-port-1", "reason": "cooldown expired"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="requeue:cand-port-1:t1"))
    assert db.row("candidates", "candidate_id", "cand-port-1")["status"] == "SCORED"


def test_outcome_projection_predicted_actual_distinct_unknown_honest(tmp_path):
    db = _fresh_db(tmp_path)
    payload_base = {"candidate_id": "cand-out-1", "mission_id": "mission-o"}
    db.append(Event.create(
        "discovery.candidate_outcome_recorded",
        {**payload_base, "predicted_value": {"score": 0.55},
         "actual_value": {"result": "PASSED"}, "window": "immediate"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="outcome:cand-out-1:immediate"))
    recorded = db.row("candidate_outcomes", "outcome_id", "cand-out-1:immediate")
    assert recorded["status"] == "RECORDED"
    import json
    assert json.loads(recorded["predicted_value_json"]) == {"score": 0.55}
    assert json.loads(recorded["actual_value_json"]) == {"result": "PASSED"}

    db.append(Event.create(
        "discovery.candidate_outcome_recorded",
        {**{"candidate_id": "cand-out-2", "mission_id": "mission-o"},
         "window": "7d"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="outcome:cand-out-2:7d"))
    unknown = db.row("candidate_outcomes", "outcome_id", "cand-out-2:7d")
    assert unknown["status"] == "UNKNOWN"


def test_full_cycle_events_reconcile_zero_drift(tmp_path):
    db = _fresh_db(tmp_path)
    events = [
        Event.create("scoring.policy_registered",
                     {"policy_version": "v3-policy-2026-08-21",
                      "weights": {}, "thresholds": {}, "hard_filters": {}},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m1"),
        Event.create("discovery.candidate_generated",
                     {"candidate_id": "cand-rec-1", "mission_id": "mission-r",
                      "source": "repo", "objective": "Fix crash now",
                      "evidence": [], "duplicate_of": None,
                      "candidate_type": "repair",
                      "expected_outcome": "no crash"},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m2"),
        Event.create("discovery.candidate_scored",
                     {"candidate_id": "cand-rec-1", "score": 0.61,
                      "policy_version": "v3-policy-2026-08-21",
                      "weights": {}, "dimensions": {}, "trace": {},
                      "hard_filters_passed": True},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m3"),
        Event.create("portfolio.evaluated",
                     {"loop_id": "loop-r", "policy_version": "v3-policy-2026-08-21",
                      "eligible_count": 1, "deferred_count": 0,
                      "rejected_count": 0, "capacity_snapshot": {}},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m4"),
        Event.create("discovery.candidate_approved",
                     {"candidate_id": "cand-rec-1", "mission_id": "mission-new",
                      "reason": "selected"},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m5"),
        Event.create("discovery.candidate_outcome_recorded",
                     {"candidate_id": "cand-rec-1", "mission_id": "mission-new",
                      "predicted_value": {"score": 0.61},
                      "actual_value": {"technical": "PASSED"},
                      "window": "immediate"},
                     source_type="supervisor", source_id="v3-test",
                     idempotency_key="m6"),
    ]
    db.append_events(events)
    report = reconcile_inspect(db)
    assert report.consistent, report.to_json()


def test_v34_database_upgrades_in_place_to_v35(tmp_path):
    from v3_fixtures import make_baseline_db
    db_path = make_baseline_db(tmp_path / "upgrade.db")
    db = FoundryDatabase(db_path)
    assert db.initialize() == SCHEMA_VERSION
    report = reconcile_inspect(db)
    assert report.consistent, report.to_json()
    with db.connect() as connection:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(candidates)")}
    assert {"fingerprint", "candidate_type", "expected_outcome",
            "provenance_hash", "expires_at", "scoring_policy_version"
            } <= columns
