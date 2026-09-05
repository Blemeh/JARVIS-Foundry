"""Command Center snapshot.discovery projection — WP-A focused tests.

Contract source: evidence/v3/operational-slice/PAYLOAD_CONTRACT.md in this
lane. Every field traces to canonical rows/events at base ceb90d7; nothing
is manufactured. Read-only, bounded, deterministic ordering.
"""
from __future__ import annotations

import json

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.v3_scoring import DIMENSIONS, POLICY_VERSION, register_policy
from jarvis_foundry.v3_outcomes import record_outcome


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    return database


def _append(database, event_type, payload, key, source_id="test"):
    return database.append(Event.create(
        event_type, payload,
        source_type="supervisor", source_id=source_id,
        idempotency_key=key))


def _hold_directive(database, directive_id="dir-hold"):
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO directives (directive_id, command_type, target,"
            " exact_text, active, last_event_id, updated_at)"
            " VALUES (?, 'STOP_EVERYTHING', 'GLOBAL', 'owner stop', 1, ?, ?)",
            (directive_id, f"e:{directive_id}",
             "2026-08-22T10:00:00+00:00"))


def _candidate_row(database, candidate_id, *, status="NEW", objective="obj",
                   fingerprint=None, candidate_type="repair",
                   expected="measurable result", evidence_count=1,
                   policy_version=None):
    now = "2026-08-22T10:00:00+00:00"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO candidates (candidate_id, mission_id, source, objective,"
            " objective_canonical, evidence_json, status, fingerprint,"
            " candidate_type, expected_outcome, scoring_policy_version,"
            " last_event_id, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, "mission-seed", "test", objective,
             objective.lower(), json.dumps([
                 {"evidence_id": f"ev-{i}"} for i in range(evidence_count)]),
             status, fingerprint or f"fp-{candidate_id}", candidate_type,
             expected, policy_version, f"seed:{candidate_id}", now))


def _mission_row(database, mission_id, created_from, *, state="PLANNED"):
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO missions (mission_id, objective, resource_id, state,"
            " acceptance_json, next_action, created_from, last_event_id,"
            " updated_at) VALUES (?, ?, 'res', ?, '[]', NULL, ?, ?, ?)",
            (mission_id, f"objective {mission_id}", state, created_from,
             f"m:{mission_id}", "2026-08-22T10:00:00+00:00"))


def _accepted_pass(database, mission_id: str) -> None:
    verdict_id = f"verdict-{mission_id}"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        independence={"session_independent": True},
        criteria=["durable pass"],
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence={"durable": True},
    )
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        checks={"durable": "accepted"},
    )


def _valid_policy(*, version=POLICY_VERSION, strategic_fit_weight=1.0):
    """Return the complete v3 policy contract required by hard-gate source."""
    return {
        "policy_version": version,
        "weights": {
            dimension: strategic_fit_weight if dimension == "strategic_fit" else 0.0
            for dimension in DIMENSIONS
        },
        "thresholds": {
            "min_total_score": 0.40,
            "min_strategic_fit": 0.0,
            "min_confidence": 0.0,
            "min_information_gain_for_research_override": 0.0,
            "comparative_wait_margin": 0.0,
        },
        "hard_filters": {"max_blast_radius": 0.85, "min_evidence_refs": 1},
    }


# --------------------------------------------------------------------------
# absence / honesty states
# --------------------------------------------------------------------------

def test_legacy_schema_without_discovery_tables_degrades_not_crashes(tmp_path):
    """A pre-v36 database must not break the whole snapshot."""
    database = FoundryDatabase(tmp_path / "legacy.sqlite")
    # Initialize to v35 by migrating stepwise, then hand-build a v35-shaped DB.
    database.initialize()
    with database.connect() as connection:
        connection.execute("DROP TABLE IF EXISTS candidate_outcomes")
        connection.execute("DROP TABLE IF EXISTS scoring_policy_registry")
        connection.execute("DROP TABLE IF EXISTS discovery_portfolio")
    snapshot = build_snapshot(database)
    assert snapshot["discovery"]["state"] == "UNSUPPORTED_SCHEMA"
    assert snapshot["discovery"]["candidates_recent"] == []
    # The rest of the snapshot still builds.
    assert "health" in snapshot and "missions" in snapshot


def test_empty_ledger_is_honest_empty(db):
    snapshot = build_snapshot(db)
    discovery = snapshot["discovery"]
    assert discovery["state"] == "EMPTY_LEDGER"
    assert discovery["flag_state"] == "UNREGISTERED"
    assert discovery["latest_cycle"] is None
    assert discovery["cycles"] == []
    assert discovery["candidates_recent"] == []
    assert discovery["selected"] is None
    assert discovery["outcomes"] == []
    assert discovery["next_action"] is None
    assert discovery["policy_version"] is None
    # cost_window is intentionally absent — never fabricated.
    assert "cost_window" not in discovery


def test_flag_states_flow_from_registered_config(db):
    unregistered = build_snapshot(db)["discovery"]["flag_state"]
    off = build_snapshot(
        db, discovery_cycle_enabled=False)["discovery"]["flag_state"]
    on = build_snapshot(db, discovery_cycle_enabled=True)["discovery"]["flag_state"]
    assert unregistered == "UNREGISTERED"
    assert off == "OFF"
    assert on == "ON"


# --------------------------------------------------------------------------
# cycle truth
# --------------------------------------------------------------------------

def test_cycle_counts_reconcile_with_ledger_decisions(db):
    _candidate_row(db, "cand-cyc-1", status="ELIGIBLE")
    _append(db, "worker_loop.cycle_started",
            {"loop_id": "L-1"}, "worker_loop.cycle_started:L-1")
    _append(db, "portfolio.evaluated",
            {"loop_id": "L-1", "policy_version": POLICY_VERSION,
             "eligible_count": 0, "deferred_count": 0,
             "rejected_count": 2},
            f"portfolio:L-1:{POLICY_VERSION}")
    _append(db, "executive.decision_recorded",
            {"decision_id": "dec-L-1", "decision": "WAIT",
             "reason": "NO_ELIGIBLE_CANDIDATES",
             "wait": {"reason": "NO_ELIGIBLE_CANDIDATES",
                      "wait_kind": "NO_ELIGIBLE_CANDIDATES"}},
            "executive.decision_recorded:dec-L-1")
    _append(db, "discovery.cycle_completed",
            {"loop_id": "L-1", "outcome": "WAITING", "observed": 3,
             "candidates_created": 2, "decision_id": "dec-L-1",
             "degraded_sources": [], "reason": "NO_ELIGIBLE_CANDIDATES"},
            "discovery.cycle_completed:L-1")

    discovery = build_snapshot(db)["discovery"]
    cycle = discovery["latest_cycle"]
    assert cycle["loop_id"] == "L-1"
    assert cycle["outcome"] == "WAITING"
    assert cycle["observed"] == 3
    assert cycle["candidates_created"] == 2
    # Counts come from the actual portfolio decisions of THIS loop.
    assert cycle["eligible"] == 0
    assert cycle["rejected"] == 2
    assert cycle["scored"] == 2
    assert cycle["wait_kind"] == "NO_ELIGIBLE_CANDIDATES"
    assert cycle["started_at"] is not None
    assert discovery["cycles"][0]["loop_id"] == "L-1"
    assert discovery["portfolio_summary"] == {
        "eligible": 0, "deferred": 0, "rejected": 2}


def test_stopped_cycle_carries_reason_and_nulls(db):
    _hold_directive(db)
    _append(db, "discovery.cycle_completed",
            {"loop_id": "L-STOP", "outcome": "STOPPED", "observed": 0,
             "candidates_created": 0, "decision_id": None,
             "degraded_sources": [],
             "reason": "STOP active: owner emergency stop"},
            "discovery.cycle_completed:L-STOP")
    discovery = build_snapshot(db)["discovery"]
    cycle = discovery["latest_cycle"]
    assert cycle["outcome"] == "STOPPED"
    assert cycle["eligible"] is None and cycle["rejected"] is None
    assert "STOP active" in cycle["reason"]
    assert cycle["wait_kind"] is None
    assert discovery["next_action"].startswith("HOLD")


# --------------------------------------------------------------------------
# candidates + scores
# --------------------------------------------------------------------------

def test_candidate_with_score_trace_and_policy(db):
    register_policy(db, _valid_policy())
    _candidate_row(db, "cand-scored", status="SCORED",
                   policy_version=POLICY_VERSION)
    dims = {"strategic_fit": 0.8, "expected_value": 0.5}
    trace = {"policy_version": POLICY_VERSION, "dimensions": dims,
             "calibration_adjust": 0.0,
             "thresholds": {"min_total_score": 0.40}}
    _append(db, "discovery.candidate_scored",
            {"candidate_id": "cand-scored", "score": 0.58,
             "policy_version": POLICY_VERSION, "dimensions": dims,
             "trace": trace},
            f"discovery.candidate_scored:cand-scored:{POLICY_VERSION}")
    # candidate_scores row is maintained by the canonical event projection;
    # the test does not duplicate it.

    discovery = build_snapshot(db)["discovery"]
    cand = next(c for c in discovery["candidates_recent"]
                if c["candidate_id"] == "cand-scored")
    # The canonical scored-event projection flips an above-threshold
    # candidate to ELIGIBLE; the projection must show row truth.
    assert cand["status"] == "ELIGIBLE"
    assert cand["score"]["total"] == 0.58
    assert cand["score"]["policy_version"] == POLICY_VERSION
    assert cand["score"]["dimensions"]["strategic_fit"] == 0.8
    assert cand["score"]["thresholds"] == {"min_total_score": 0.40}
    assert cand["rejection"] is None
    assert discovery["policy_version"] == POLICY_VERSION


def test_rejected_candidate_carries_code_and_detail(db):
    _candidate_row(db, "cand-rej", status="REJECTED")
    _append(db, "discovery.candidate_rejected",
            {"candidate_id": "cand-rej",
             "reason": "HARD_NO_EVIDENCE: no evidence refs",
             "filter_or_score": "filter",
             "reason_code": "HARD_NO_EVIDENCE"},
            "discovery.candidate_rejected:cand-rej:HARD_NO_EVIDENCE")
    discovery = build_snapshot(db)["discovery"]
    cand = discovery["candidates_recent"][0]
    assert cand["status"] == "REJECTED"
    assert cand["rejection"]["code"] == "HARD_NO_EVIDENCE"
    assert "no evidence refs" in cand["rejection"]["detail"]


def test_malformed_optional_json_degrades_to_partial(db):
    _candidate_row(db, "cand-bad-json", status="NEW")
    with db.connect() as connection:
        connection.execute(
            "UPDATE candidates SET evidence_json = '{not json' "
            "WHERE candidate_id = 'cand-bad-json'")
    snapshot = build_snapshot(db)
    discovery = snapshot["discovery"]
    cand = discovery["candidates_recent"][0]
    assert cand["partial"] is True
    assert cand["evidence_ref_count"] == 0
    assert discovery["state"] == "PARTIAL"
    # Snapshot remains JSON-safe despite malformed stored text.
    json.dumps(snapshot)


def test_candidate_lineage_to_mission(db):
    _mission_row(db, "M-LINEAGE", "candidate:cand-line")
    _candidate_row(db, "cand-line", status="EXECUTED")
    discovery = build_snapshot(db)["discovery"]
    cand = discovery["candidates_recent"][0]
    assert cand["mission_id"] == "M-LINEAGE"


def test_sql_injection_text_treated_as_data(db):
    hostile = "x'); DROP TABLE candidates; --"
    _candidate_row(db, "cand-sqli", objective=hostile, status="NEW")
    discovery = build_snapshot(db)["discovery"]
    cand = discovery["candidates_recent"][0]
    assert cand["objective"] == hostile
    # Table survived: parameterized read path proven.
    assert len(discovery["candidates_recent"]) == 1


# --------------------------------------------------------------------------
# selection + lineage
# --------------------------------------------------------------------------

def test_selected_decision_with_alternatives_and_materialization(db):
    _mission_row(db, "M-SEL", "candidate:cand-win")
    _append(db, "executive.decision_recorded",
            {"decision_id": "dec-sel", "decision": "SELECT",
             "reason": "selected cand-win over 1 alternatives",
             "candidate_id": "cand-win",
             "selected_score": {"composite": 0.61, "goal_fit": 0.5},
             "rejected": [{"candidate_id": "cand-lose",
                           "composite": 0.44,
                           "reason": "ranked below selected"}],
             "deferred": [{"candidate_id": "cand-defer",
                           "reason": "stale_historical"}]},
            "executive.decision_recorded:dec-sel")
    _append(db, "executive.decision_materialized",
            {"decision_id": "dec-sel", "decision": "SELECT",
             "candidate_id": "cand-win", "mission_id": "M-SEL"},
            "materialize:dec-sel")
    _candidate_row(db, "cand-win", status="EXECUTED")

    discovery = build_snapshot(db)["discovery"]
    sel = discovery["selected"]
    assert sel["decision_id"] == "dec-sel"
    assert sel["candidate_id"] == "cand-win"
    assert sel["total_score"] == 0.61
    assert sel["mission_id"] == "M-SEL"
    assert sel["block_reason"] is None
    assert sel["rejected_alternatives"][0]["candidate_id"] == "cand-lose"
    assert sel["rejected_alternatives"][0]["total_score"] == 0.44
    assert sel["deferred_alternatives"][0]["candidate_id"] == "cand-defer"


def test_materialization_blocked_surfaces_block_reason(db):
    _append(db, "executive.decision_recorded",
            {"decision_id": "dec-blk", "decision": "SELECT",
             "reason": "selected cand-b", "candidate_id": "cand-b",
             "selected_score": {"composite": 0.5}, "rejected": [],
             "deferred": []},
            "executive.decision_recorded:dec-blk")
    _append(db, "executive.materialization_blocked",
            {"decision_id": "dec-blk", "candidate_id": "cand-b",
             "block_reason": "missing rollback plan"},
            "materialize_blocked:dec-blk:missing_rollback_plan")
    discovery = build_snapshot(db)["discovery"]
    assert discovery["selected"]["block_reason"] == "missing rollback plan"
    assert discovery["selected"]["mission_id"] is None


# --------------------------------------------------------------------------
# team / verifier / outcomes
# --------------------------------------------------------------------------

def test_team_and_verifier_for_selected_mission(db):
    _append(db, "executive.decision_recorded",
            {"decision_id": "dec-team", "decision": "SELECT",
             "candidate_id": "cand-t", "reason": "r",
             "selected_score": {"composite": 0.6}, "rejected": [],
             "deferred": []},
            "executive.decision_recorded:dec-team")
    _append(db, "executive.decision_materialized",
            {"decision_id": "dec-team", "decision": "SELECT",
             "candidate_id": "cand-t", "mission_id": "M-TEAM"},
            "materialize:dec-team")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO agent_runs (run_id, mission_id, step_id, role,"
            " provider, model, tool_policy_json, status, started_at,"
            " last_event_id, updated_at)"
            " VALUES ('run-b1', 'M-TEAM', 'step-a', 'builder', 'prov-x',"
            " 'model-x', '{}', 'COMPLETED', '2026-08-22T10:00:00Z', 'e', 'e'),"
            "        ('run-v1', 'M-TEAM', 'step-a', 'verifier', 'prov-y',"
            " 'model-y', '{}', 'COMPLETED', '2026-08-22T10:05:00Z', 'e', 'e')")
        connection.execute(
            "INSERT INTO verification_verdicts (verdict_id, mission_id,"
            " subject_type, subject_id, status, verdict, verifier_run_id,"
            " builder_run_id, independence_json, criteria_json,"
            " findings_json, evidence_json, missing_proof_json,"
            " scope_violations_json, repair_requirements_json, reason,"
            " last_event_id, updated_at)"
            " VALUES ('v-1', 'M-TEAM', 'mission_step', 'M-TEAM:step-a',"
            " 'PASS', 'PASS', 'run-v1', 'run-b1', ?, '[]', '[]', '{}',"
            " '[]', '[]', '[]', NULL, 'e', 'e')",
            (json.dumps({"fresh_session": True,
                         "independence": "REDUCED",
                         "provider": "prov-y"}),))

    discovery = build_snapshot(db)["discovery"]
    roles = sorted(m["role"] for m in discovery["active_team"])
    assert roles == ["builder", "verifier"]
    verifier = discovery["verifier"]
    assert verifier["verdict"] == "PASS"
    assert verifier["fresh_session"] is True
    assert verifier["independence"] == "REDUCED"
    assert verifier["repair_rounds_used"] == 0


def test_outcome_predicted_actual_distinct_unknown_honest(db):
    _candidate_row(db, "cand-out", status="EXECUTED")
    _mission_row(db, "M-OUT", "candidate:cand-out", state="COMPLETE")
    _append(db, "mission.completed",
            {"mission_id": "M-OUT", "outcome": {"technical": "PASSED"}},
            "mission.completed:M-OUT")
    _accepted_pass(db, "M-OUT")
    record_outcome(
        db, candidate_id="cand-out", mission_id="M-OUT",
        mission_outcome="PASSED",
        predicted_value={"expected_success": 0.64})
    discovery = build_snapshot(db)["discovery"]
    outcome = discovery["outcomes"][0]
    assert outcome["status"] == "UNKNOWN"
    assert outcome["actual"] is None          # honest UNKNOWN, not {}
    assert outcome["predicted"] == {"expected_success": 0.64}
    assert outcome["delta"] is None

    _candidate_row(db, "cand-out2", status="EXECUTED")
    _mission_row(db, "M-OUT2", "candidate:cand-out2", state="COMPLETE")
    _append(db, "mission.completed",
            {"mission_id": "M-OUT2", "outcome": {"technical": "PASSED"}},
            "mission.completed:M-OUT2")
    _accepted_pass(db, "M-OUT2")
    record_outcome(
        db, candidate_id="cand-out2", mission_id="M-OUT2",
        mission_outcome="PASSED",
        predicted_value={"score": 0.7}, actual_value={"score": 0.65})
    discovery = build_snapshot(db)["discovery"]
    recorded = next(o for o in discovery["outcomes"]
                    if o["candidate_id"] == "cand-out2")
    assert recorded["status"] == "RECORDED"
    assert recorded["actual"] == {"score": 0.65}
    assert recorded["delta"] == round(0.65 - 0.70, 4)


# --------------------------------------------------------------------------
# ordering, bounds, determinism
# --------------------------------------------------------------------------

def test_cycles_newest_first_bounded_deterministic(db):
    for i in range(15):
        _append(db, "discovery.cycle_completed",
                {"loop_id": f"L-{i:02d}", "outcome": "WAITING",
                 "observed": i, "candidates_created": 0,
                 "decision_id": None, "degraded_sources": [], "reason": "r"},
                f"discovery.cycle_completed:L-{i:02d}")
    discovery = build_snapshot(db)["discovery"]
    ids = [c["loop_id"] for c in discovery["cycles"]]
    assert len(ids) <= 10
    assert ids[0] == "L-14"           # newest first
    # Deterministic: identical rebuild gives identical order.
    again = build_snapshot(db)["discovery"]
    assert [c["loop_id"] for c in again["cycles"]] == ids


def test_candidates_bounded_and_stable_ordering(db):
    for i in range(30):
        _candidate_row(db, f"cand-bound-{i:02d}")
    discovery = build_snapshot(db)["discovery"]
    ids = [c["candidate_id"] for c in discovery["candidates_recent"]]
    assert len(ids) <= 25
    assert ids[0] == "cand-bound-29"   # newest updated_at first; tie -> newest id


def test_existing_non_discovery_consumers_unchanged(db):
    """The additive section must not disturb existing keys/shapes."""
    _candidate_row(db, "cand-stable")
    pinned = build_snapshot(db, now="2026-08-22T12:00:00Z")
    legacy_keys = [k for k in pinned if k != "discovery"]
    # Every pre-existing top-level key survives unchanged.
    expected = {
        "generated_at", "source_of_truth", "supervisor", "system_state",
        "stop", "controls", "missions", "workers", "evidence",
        "verification", "health", "resources", "systems", "candidates",
        "mission_steps", "memory", "procedures", "release_identity",
        "releases_canaries_rollbacks", "recovery", "costs", "briefings",
            "voice", "timeline", "executive",
            "owner_directives",
                "decision_queue",
                "result_traceability",
                "briefing_packet",
                "effect_truth",
                "evolution_truth",
                "portfolio",
                "capability_fabric",
                "vault",
            }
    assert set(legacy_keys) == expected
    assert set(pinned["candidates"][0]) >= {"candidate_id"}
    assert pinned["vault"] == {"files": [], "count": 0}
    assert pinned["generated_at"] == "2026-08-22T12:00:00Z"
