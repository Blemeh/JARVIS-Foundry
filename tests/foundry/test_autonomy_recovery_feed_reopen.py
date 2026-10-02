"""Feed reopen of terminal portfolio items (recovery-budget bounded).

Live 2026-10-02 19:50 UTC: discovery accepted the CI_FAILURE opportunity
(new candidate cand-bf11245e612a — the D4b duplicate release worked), but
`feed_portfolio` failed with an idempotency conflict on
`portfolio.work_registered:pw-dupkey-...`: the deterministic item id already
exists, terminal-BLOCKED with the dead mission's lineage.

Repair: when re-registration conflicts on a TERMINAL (BLOCKED/CANCELLED)
item and the recovery budget still allows it (same bound as the dedup
release: exactly one failed lineage, no recovery attempted yet), the feed
reopens the item — new candidate linkage, refreshed evidence/provenance,
status OPEN — instead of stranding the accepted opportunity.  Live
(OPEN/CONSUMED/PLANNED/ELIGIBLE) items keep the honest conflict error.
A second terminal failure re-locks: reopen refused, conflict stands.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.project_discovery import Opportunity, feed_portfolio

from worker_test_helpers import mission_database

PROBLEM = ("fix failing CI on blemeh/jarvis-foundry: workflow "
           "'.github/workflows/windows-ci.yml' concluded failure at "
           "2026-08-19T03:42:59Z; changes currently land without CI "
           "verification")
OBJECTIVE = f"CI_FAILURE: {PROBLEM}"
DUPKEY = ("dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92")
ITEM_ID = f"pw-{DUPKEY}"


def _opportunity() -> Opportunity:
    return Opportunity(
        kind="CI_FAILURE",
        repository="blemeh/jarvis-foundry",
        problem_statement=PROBLEM,
        evidence_refs=("github:blemeh/jarvis-foundry@main",),
        expected_value=0.5,
        confidence=0.8,
        effort=0.3,
        risk=0.2,
        reversibility=0.9,
        urgency=0.4,
        information_gain=0.5,
        why_now="CI failure still present on the branch",
        duplicate_key=DUPKEY,
    )


def _register_live_item(database, *, candidate_id: str = "cand-old",
                        objective_id: str | None = None):
    from jarvis_foundry.evolution.executive_portfolio import register_work_item

    metadata = {"repository": "blemeh/jarvis-foundry"}
    if objective_id:
        metadata["executive_objective_id"] = objective_id
    register_work_item(
        database,
        item_id=ITEM_ID,
        kind="REPAIR",
        objective=OBJECTIVE,
        candidate_id=candidate_id,
        resource_id="resource-1",
        expected_value=0.5,
        urgency=0.4,
        information_gain=0.5,
        confidence=0.8,
        effort=0.3,
        risk=0.2,
        reversibility=0.9,
        evidence_refs=["github:blemeh/jarvis-foundry@main"],
        provenance={
            "source_lane": "live-github-research-activation",
            "repository": "blemeh/jarvis-foundry",
            "opportunity_kind": "CI_FAILURE",
            "problem_statement": PROBLEM,
            "duplicate_key": DUPKEY,
            "why_now": "CI failure observed",
        },
        metadata=metadata,
        source_id="test",
    )


def _set_status(database, status: str, *, reason: str = "test") -> None:
    from jarvis_foundry.evolution.executive_portfolio import set_work_status

    set_work_status(database, ITEM_ID, status, reason=reason,
                    source_id="test")


def _failed_mission(database, *, mission_id: str) -> None:
    from jarvis_foundry.events import Event

    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": OBJECTIVE,
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["green"], "next_action": "execute",
         "created_from": "portfolio:test"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    states = ["ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING",
              "BLOCKED"]
    for index, state in enumerate(states):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state,
             "next_action": "execute"},
            source_type="supervisor", source_id="test",
            idempotency_key=f"mission.state_changed:{mission_id}:{state}:{index}",
        ))


def _item(database):
    from jarvis_foundry.evolution.executive_portfolio import (
        rebuild_portfolio_state,
    )

    return rebuild_portfolio_state(database).work_items[ITEM_ID]


def test_terminal_item_reopens_for_recovery(tmp_path: Path) -> None:
    """The live shape: BLOCKED item + one BLOCKED mission → feed reopens."""
    database, _repo = mission_database(tmp_path)
    _register_live_item(database)
    _set_status(database, "CONSUMED", reason="materialized:mission-old")
    _set_status(database, "BLOCKED", reason="mission mission-old terminal")
    _failed_mission(database, mission_id="mission-old")

    result = feed_portfolio(database, "portfolio-discovery:test",
                            [_opportunity()], resource_id="resource-1")
    assert len(result["fed"]) == 1
    entry = result["fed"][0]
    assert "portfolio_error" not in entry, (
        f"recovery reopen refused: {entry.get('portfolio_error')}")
    assert entry.get("portfolio_item_id") == ITEM_ID

    item = _item(database)
    assert item.status == "OPEN", f"item must reopen, got {item.status}"
    assert item.candidate_id is not None
    assert item.candidate_id != "cand-old", (
        "reopen must link the NEW candidate, not the dead one")


def test_live_item_conflict_stands(tmp_path: Path) -> None:
    """A CONSUMED item (mission may still run) keeps the honest conflict."""
    database, _repo = mission_database(tmp_path)
    _register_live_item(database)
    _set_status(database, "CONSUMED", reason="materialized:mission-live")

    result = feed_portfolio(database, "portfolio-discovery:test",
                            [_opportunity()], resource_id="resource-1")
    entry = result["fed"][0]
    assert "portfolio_error" in entry, (
        "live item conflict must stay an honest error")
    assert _item(database).status == "CONSUMED"


def test_second_recovery_refused_after_two_terminal_failures(
        tmp_path: Path) -> None:
    """Original + recovery both terminal → budget exhausted, conflict stands."""
    database, _repo = mission_database(tmp_path)
    _register_live_item(database)
    _set_status(database, "CONSUMED", reason="materialized:mission-old")
    _set_status(database, "BLOCKED", reason="mission mission-old terminal")
    _failed_mission(database, mission_id="mission-old")
    _failed_mission(database, mission_id="mission-recovery")

    result = feed_portfolio(database, "portfolio-discovery:test",
                            [_opportunity()], resource_id="resource-1")
    entry = result["fed"][0]
    assert "portfolio_error" in entry, (
        "exhausted recovery budget must not reopen")
    assert _item(database).status == "BLOCKED"


def test_reopen_preserves_executive_binding(tmp_path: Path) -> None:
    """The declaring objective linkage survives the reopen."""
    database, _repo = mission_database(tmp_path)
    _register_live_item(database,
                        objective_id="owner-goal:owner-c4dbe49e2dd7b49e11583655")
    _set_status(database, "CONSUMED", reason="materialized:mission-old")
    _set_status(database, "BLOCKED", reason="mission mission-old terminal")
    _failed_mission(database, mission_id="mission-old")

    result = feed_portfolio(
        database, "portfolio-discovery:test", [_opportunity()],
        resource_id="resource-1",
        executive_objective_id="owner-goal:owner-c4dbe49e2dd7b49e11583655")
    assert "portfolio_error" not in result["fed"][0]
    metadata = dict(_item(database).metadata or {})
    assert metadata.get("executive_objective_id") == (
        "owner-goal:owner-c4dbe49e2dd7b49e11583655")
