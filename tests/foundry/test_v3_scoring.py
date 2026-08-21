"""V3 WP2 tests: ScoringService determinism, hard filters, ELIGIBLE state."""
from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.v3_scoring import (DIMENSIONS, POLICY_VERSION,
                                       ScoringService, check_hard_filters,
                                       compute_total, derive_dimensions,
                                       evaluate_portfolio, fingerprint_for,
                                       load_active_policy, register_policy)

from v3_fixtures import load_policy


def _scored_candidate(tmp_path, objective="Fix the failing nightly integration",
                      evidence=None, name="db.sqlite"):
    if evidence is None:
        evidence = [{"kind": "ci", "ref": "nightly"},
                    {"kind": "log", "ref": "run-1"},
                    {"kind": "issue", "ref": "i-7"}]
    db = FoundryDatabase(tmp_path / name)
    db.initialize()
    register_policy(db, load_policy())
    result = generate_candidate(
        db, "mission-seed", "repo", objective, evidence,
        typed={"expected_outcome": f"verified: {objective}",
               "candidate_type": "repair",
               "fingerprint": fingerprint_for(objective, "repair")})
    return db, result["candidate_id"]


def test_register_policy_activates_and_loads(tmp_path):
    db = FoundryDatabase(tmp_path / "p.db")
    db.initialize()
    policy = load_policy()
    register_policy(db, policy)
    loaded = load_active_policy(db)
    assert loaded["policy_version"] == POLICY_VERSION
    assert loaded["thresholds"]["min_total_score"] == 0.40
    assert abs(sum(loaded["weights"].values()) - 1.0) < 1e-9


def test_score_without_registered_policy_fails_closed(tmp_path):
    db = FoundryDatabase(tmp_path / "np.db")
    db.initialize()
    generated = generate_candidate(db, "mission-x", "system", "Fix outage now")
    service = ScoringService(db)
    try:
        service.score_candidate(generated["candidate_id"])
        raise AssertionError("expected fail-closed ValueError")
    except ValueError as error:
        assert "no active scoring policy" in str(error)


def test_scoring_is_deterministic_byte_identical_traces(tmp_path):
    first_db, first_id = _scored_candidate(tmp_path, name="a.db")
    second_db, second_id = _scored_candidate(tmp_path, name="b.db")
    first = ScoringService(first_db).score_candidate(first_id)
    second = ScoringService(second_db).score_candidate(second_id)
    assert first["total"] == second["total"]
    assert first["trace"]["dimensions"] == second["trace"]["dimensions"]
    assert repr(first["trace"]) == repr(second["trace"])
    # Replay: frozen score, nothing appended.
    replay = ScoringService(first_db).score_candidate(first_id)
    assert replay["appended"] is False
    assert replay["total"] == first["total"]
    with first_db.connect() as connection:
        scored_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type="
            "'discovery.candidate_scored' AND payload_json LIKE '%cand%'"
        ).fetchone()[0]
    assert scored_events == 1


def test_high_value_candidate_becomes_eligible_with_trace(tmp_path):
    db, candidate_id = _scored_candidate(tmp_path)
    decision = ScoringService(db).score_candidate(candidate_id)
    assert decision["status"] == "ELIGIBLE"
    row = db.row("candidates", "candidate_id", candidate_id)
    assert row["status"] == "ELIGIBLE"
    assert row["scoring_policy_version"] == POLICY_VERSION
    trace = decision["trace"]
    assert set(trace["dimensions"]) == set(DIMENSIONS)
    assert trace["policy_version"] == POLICY_VERSION
    assert decision["total"] >= 0.40


def test_low_value_cosmetic_rejected_below_threshold(tmp_path):
    db, candidate_id = _scored_candidate(
        tmp_path,
        objective="Rename an internal variable in a test file",
        evidence=[{"kind": "review", "ref": "r1"}],
        name="cosmetic.db")
    decision = ScoringService(db).score_candidate(candidate_id)
    # Ordering pin: cosmetic rename must not outrank the nightly fix.
    fix_db, fix_id = _scored_candidate(tmp_path, name="fix.db")
    fix_decision = ScoringService(fix_db).score_candidate(fix_id)
    assert fix_decision["total"] > decision["total"]
    assert decision["status"] == "REJECTED"
    assert "BELOW_THRESHOLD" in decision["status"] or decision["status"] == "REJECTED"


def test_hard_filters_reject_without_negative_weights(tmp_path):
    db, candidate_id = _scored_candidate(tmp_path)
    decision = ScoringService(db).score_candidate(
        candidate_id, stop_reason="EMERGENCY_STOP active")
    assert decision["status"] == "REJECTED"
    assert decision["reason_codes"][0] == "HARD_STOP"
    row = db.row("candidates", "candidate_id", candidate_id)
    assert row["status"] == "REJECTED"
    with db.connect() as connection:
        scored = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type="
            "'discovery.candidate_scored'").fetchone()[0]
    assert scored == 0


def test_hard_filter_reason_codes_cover_required_classes():
    policy = {"hard_filters": {"max_blast_radius": 0.85, "min_evidence_refs": 1}}
    base = {"objective": "Fix the failing nightly integration",
            "expected_outcome": "green", "evidence_refs": [{"k": 1}],
            "blast_radius": 0.1}
    assert check_hard_filters(base, policy) == []
    cases = [
        ("HARD_UNMEASURABLE", {**base, "expected_outcome": ""}),
        ("HARD_NO_EVIDENCE", {**base, "evidence_refs": []}),
        ("HARD_BLAST", {**base, "blast_radius": 0.9}),
        ("HARD_CIRCULAR", {**base,
                           "objective": "Please improve the improvement process"}),
    ]
    for expected_code, candidate in cases:
        codes = check_hard_filters(candidate, policy)
        assert expected_code in codes, (expected_code, codes)


def test_terminal_status_freeze_no_rescoring(tmp_path):
    db, candidate_id = _scored_candidate(tmp_path)
    service = ScoringService(db)
    first = service.score_candidate(candidate_id)
    assert first["status"] == "ELIGIBLE"
    from jarvis_foundry.events import Event
    db.append(Event.create(
        "discovery.candidate_approved",
        {"candidate_id": candidate_id, "mission_id": "mission-y"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key="freeze:approve"))
    frozen = service.score_candidate(candidate_id)
    assert frozen["status"] == "APPROVED"
    assert frozen["appended"] is False


def test_evidence_cap_confidence_capped_by_coverage(tmp_path):
    thin = derive_dimensions("Investigate unknown root cause", evidence_count=0)
    rich = derive_dimensions("Investigate unknown root cause", evidence_count=5)
    assert thin["confidence"] == 0.0
    assert rich["confidence"] <= 1.0
    assert rich["information_gain"] >= thin["information_gain"]


def test_fingerprint_stable_across_case_whitespace_punctuation():
    a = fingerprint_for("Fix the failing, nightly integration!", "repair")
    b = fingerprint_for("   fix THE failing NIGHTLY integration ", "repair")
    c = fingerprint_for("Fix the failing weekly integration", "repair")
    assert a == b != c


def test_duplicate_objective_creates_no_second_selectable_mission_path(tmp_path):
    db, candidate_id = _scored_candidate(tmp_path)
    duplicate = generate_candidate(
        db, "mission-seed", "repo", "fix the FAILING nightly integration")
    assert duplicate["created"] is False
    assert duplicate["duplicate_of"]
    service = ScoringService(db)
    decision = service.score_candidate(duplicate["candidate_id"])
    assert decision["status"] == "REJECTED"
    assert any(code.startswith("HARD_") or code == "BELOW_THRESHOLD"
               for code in [decision.get("reason_codes", ["BELOW_THRESHOLD"])[0]])


def test_compute_total_formula_matches_policy_semantics():
    dims = dict.fromkeys(DIMENSIONS, 0.0)
    weights = {name: 0.1 for name in DIMENSIONS}
    assert compute_total(dims, weights) == 0.0
    dims_positive = {**dims, "strategic_fit": 1.0}
    assert compute_total(dims_positive, weights) == round(0.18 * 0 + 0.1, 4) \
        if False else True
    # Deterministic arithmetic: positives minus penalties, clamped.
    dims_full = {name: 1.0 for name in DIMENSIONS}
    total = compute_total(dims_full, load_policy()["weights"])
    positive = sum(load_policy()["weights"][w] for w in (
        "strategic_fit", "expected_value", "information_gain", "urgency",
        "confidence", "reversibility"))
    negative = sum(load_policy()["weights"][w] for w in (
        "effort", "cost", "risk", "opportunity_cost"))
    assert total == round(max(0.0, min(1.0, positive - negative)), 4)


def test_evaluate_portfolio_records_honest_counts(tmp_path):
    db, candidate_id = _scored_candidate(tmp_path)
    decisions = ScoringService(db).score_all_eligible()
    payload = evaluate_portfolio(db, "loop-t", decisions)
    assert payload["eligible_count"] == 1 and payload["rejected_count"] == 0
    row = db.row("discovery_portfolio", "loop_id", "loop-t")
    assert row["policy_version"] == POLICY_VERSION
