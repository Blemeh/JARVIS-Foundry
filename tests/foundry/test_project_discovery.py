"""Project discovery — inventory, observations, dedupe, V7A bridge."""

from __future__ import annotations

from datetime import datetime, timezone

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.project_discovery import (
    Opportunity,
    RepositoryRecord,
    detect_opportunities,
    feed_portfolio,
    observe_repository,
    reject_duplicates,
)


def _record(**overrides) -> RepositoryRecord:
    base = dict(
        full_name="Blemeth/Hoowen",
        visibility="PRIVATE",
        default_branch="main",
        head_sha="a" * 40,
        pushed_at="2026-08-24T18:08:24Z",
        language="Python",
        issues_enabled=True,
        open_issues_count=0,
        ci_runs=({"name": "CI", "status": "completed",
                  "conclusion": "failure",
                  "created_at": "2026-08-24T18:08:26Z"},),
        releases=(),
    )
    base.update(overrides)
    return RepositoryRecord(**base)


def test_inventory_record_preserves_exact_remote_identity() -> None:
    record = _record()
    payload = record.to_payload()
    assert payload["resource_key"] == "github:Blemeth/Hoowen"
    assert payload["provider"] == "github"
    assert len(payload["head_sha"]) == 40
    assert payload["default_branch"] == "main"


def test_observations_cover_drift_ci_staleness_and_recurring_failures() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    stale = _record(
        full_name="Blemeth/Old",
        pushed_at="2026-06-01T00:00:00Z",
        ci_runs=(),
        head_sha=None,
    )
    observations = observe_repository(
        stale, previous_head_sha="b" * 40, previous_pushed_at=None, now=now)
    types = {obs.observation_type for obs in observations}
    # drift needs a current head; absent head means no drift claim — honest.
    assert "DEFAULT_BRANCH_DRIFT" not in types
    assert "NO_CI_ON_DEFAULT" in types
    assert "STALE_REPO" in types

    failing = _record(ci_runs=(
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-24T18:08:26Z"},
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-05T20:19:53Z"},
    ))
    recurring = observe_repository(failing, now=now)
    ci_observations = [o for o in recurring if o.observation_type == "CI_FAILED"]
    assert len(ci_observations) == 2  # single failure + recurring pattern


def test_opportunities_are_typed_with_duplicate_keys_and_values() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    failing = _record(ci_runs=(
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-24T18:08:26Z"},
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-05T20:19:53Z"},
    ))
    opportunities = detect_opportunities(
        observe_repository(failing, now=now), failing)
    ci = [o for o in opportunities if o.kind == "CI_FAILURE"]
    assert len(ci) == 1
    opp = ci[0]
    assert opp.duplicate_key.startswith("dupkey-Blemeth/Hoowen-CI_FAILURE-")
    assert opp.repository == "Blemeth/Hoowen"
    assert opp.evidence_refs
    assert opp.why_now
    payload = opp.to_payload()
    for required in ("expected_value", "confidence", "effort", "risk",
                     "reversibility", "urgency", "information_gain"):
        assert isinstance(payload[required], float)


def test_reject_duplicates_against_candidates_issues_and_batch(tmp_path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    from jarvis_foundry.discovery_candidates import generate_candidate
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    failing = _record(ci_runs=(
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-24T18:08:26Z"},
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-05T20:19:53Z"},
    ))
    opportunities = detect_opportunities(
        observe_repository(failing, now=now), failing)
    # Pre-existing candidate with the same canonical objective:
    existing = opportunities[0]
    generate_candidate(db, mission_id="mission-earlier", source="repo",
                       objective=existing.objective())
    accepted, rejected = reject_duplicates(db, "mission-lane", opportunities)
    assert not accepted
    assert any("already covered" in r.reason for r in rejected)

    fresh_repo = _record(full_name="Blemeth/LeadGen", ci_runs=())
    fresh = detect_opportunities(observe_repository(fresh_repo, now=now),
                                 fresh_repo)
    doubled = list(fresh) + list(fresh)
    _accepted2, rejected2 = reject_duplicates(db, "mission-lane", doubled)
    assert any("duplicate within discovery batch" in r.reason
               for r in rejected2)
    _accepted3, rejected3 = reject_duplicates(
        db, "mission-lane", fresh,
        open_issue_titles_by_repo={
            "Blemeth/LeadGen": ["No CI coverage on the default branch"]})
    assert any("open GitHub issue" in r.reason for r in rejected3)
    accepted4, rejected4 = reject_duplicates(
        db, "mission-lane", fresh,
        already_solved_on_default=lambda opp: True)
    assert not accepted4
    assert all("default branch" in r.reason for r in rejected4)


def test_feed_portfolio_creates_scored_candidates_and_v7a_items(tmp_path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    failing = _record(ci_runs=(
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-24T18:08:26Z"},
        {"name": "CI", "status": "completed", "conclusion": "failure",
         "created_at": "2026-08-05T20:19:53Z"},
    ))
    opportunities = detect_opportunities(
        observe_repository(failing, now=now), failing)
    result = feed_portfolio(db, "mission-discovery-e2e", opportunities)
    assert result["decision"] == "SELECT_CANDIDATES"
    fed = result["fed"]
    assert fed and all("candidate_id" in entry for entry in fed)
    with_scores = [entry for entry in fed if "score" in entry]
    assert with_scores
    with_items = [entry for entry in fed if "portfolio_item_id" in entry]
    assert with_items
    with db.connect() as connection:
        candidates = connection.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0]
        items = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'portfolio.work_registered'").fetchone()[0]
    assert candidates >= 1
    assert items >= 1
    replay = feed_portfolio(db, "mission-discovery-e2e", opportunities)
    duplicate_fed = [entry for entry in replay["fed"]
                     if entry.get("duplicate_of")]
    assert duplicate_fed or all(not entry.get("portfolio_item_id")
                                for entry in replay["fed"])


def test_honest_wait_when_nothing_warrants_work(tmp_path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    low_value = Opportunity(
        kind="MAINTENANCE",
        repository="Blemeth/Quiet",
        problem_statement="repository has no push activity for weeks; "
                          "decide resume archive or deliberate keep",
        evidence_refs=("github:Blemeth/Quiet@main",),
        expected_value=0.05, confidence=0.8, effort=0.02, risk=0.01,
        reversibility=1.0, urgency=0.0, information_gain=0.05,
        why_now="stale estate wastes discovery reads",
        duplicate_key="dupkey-Blemeth/Quiet-MAINTENANCE-x",
    )
    result = feed_portfolio(db, "mission-wait-case", [low_value])
    assert result["decision"] in {"WAIT"}
    assert result["wait_reason"]
