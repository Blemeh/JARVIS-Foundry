"""The starvation lane must CONSUME an existing OPEN opportunity (P0-3).

Live 2026-10-02/03, canonical `639ec57`, the only live recovery opportunity
was `pw-dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92` (REPAIR, OPEN,
`resource-jarvis-canonical`, bound to the governing owner goal).  Every other
edge of the vertical had already been repaired, and the scoped production
pool held exactly that one item, eligible, with an exact resource binding.

It still never reached a mission, for a reason that had nothing to do with
the item:

``_run_discovery_starvation_fallback`` gated the whole lane on ``fed_any`` --
"did THIS pass register something NEW?" -- computed only from the discovery
report and evaluated BEFORE the canonical selector ran.  Re-deriving an
already-registered opportunity raises the honest idempotency conflict;
``_reopen_terminal_item_for_recovery`` then correctly refuses because the
item is OPEN, not terminal.  So the feed entry carries ``portfolio_error`` and
no ``portfolio_item_id``, ``fed_any`` is False, and the lane returned
``(None, None)`` without ever calling ``select_next_work``.

First broken edge: the lane tested the wrong predicate.  It asked whether
discovery mutated state this pass instead of whether an admissible item
exists in the lane scope, so an opportunity the lane already owned could
never be consumed.  Ledger 345448-345450 shows the same lane reaching the
selector on the pass where the reopen succeeded, and being rejected there on
its own recovery narrative (D5, repaired in e63083d) -- the gate simply moves
the failure one step earlier.

Refusing a duplicate registration and refusing to reopen a live item are the
CORRECT idempotent outcomes.  They must not hide work that is already
registered, already OPEN and consumable.

These tests are authored BEFORE the repair and must fail on `9a4c893`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.evolution.executive_portfolio import (
    rebuild_portfolio_state,
    register_work_item,
)

from worker_test_helpers import make_repository

PROBLEM = ("fix failing CI on blemeh/jarvis-foundry: workflow "
           "'.github/workflows/windows-ci.yml' concluded failure at "
           "2026-08-19T03:42:59Z; changes currently land without CI "
           "verification")
OBJECTIVE = f"CI_FAILURE: {PROBLEM}"
DUPKEY = "dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92"
ITEM_ID = f"pw-{DUPKEY}"
OBJECTIVE_ID = "owner-goal:owner-c4dbe49e2dd7b49e11583655"
CYCLE_ID = "continuous:probe-consume"


def _database(tmp_path: Path):
    """Bare initialized database with the registered git resource.

    No seeded ACTIVE mission: the default ``max_concurrent_missions == 1``
    would otherwise be consumed and mask the gate under test behind
    ``capacity:concurrent_missions``.  The registered resource is required
    because discovery-opportunity materialization fails closed on an
    unregistered binding -- the live registry shape.
    """
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.events import Event

    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-1")))
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build"))
    return database


def _open_item(database) -> None:
    """The exact live end state: registered, OPEN, exactly resource-bound."""
    register_work_item(
        database, item_id=ITEM_ID, kind="REPAIR", objective=OBJECTIVE,
        candidate_id="cand-40b81c46a1bd", resource_id="resource-1",
        expected_value=0.5, urgency=0.4, information_gain=0.5,
        confidence=0.8, effort=0.3, risk=0.2, reversibility=0.9,
        evidence_refs=["github:blemeh/jarvis-foundry@main"],
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "blemeh/jarvis-foundry",
                    "opportunity_kind": "CI_FAILURE",
                    "problem_statement": PROBLEM, "duplicate_key": DUPKEY,
                    "why_now": "CI failure still present on the branch"},
        metadata={"repository": "blemeh/jarvis-foundry",
                  "executive_objective_id": OBJECTIVE_ID},
        source_id="test",
    )


def _refusing_feed(monkeypatch):
    """Discovery that re-derives the SAME opportunity.

    This is the live condition: registration collides on the deterministic
    ``pw-{duplicate_key}`` id, and the bounded reopen refuses because the
    existing item is OPEN rather than terminal.  Both refusals are correct,
    so the report has no ``portfolio_item_id`` and the old ``fed_any`` gate
    evaluated False.
    """
    from jarvis_foundry import project_discovery

    def _discover(database, *, objective_id, source_id, executive_objective_id,
                  **_kwargs):
        return {"repos": [{
            "repository": "blemeh/jarvis-foundry",
            "opportunities": 1,
            "fed": [{
                "duplicate_key": DUPKEY,
                "candidate_id": "cand-a029008dae70",
                "score": 0.275,
                "portfolio_error": (
                    "idempotency conflict for portfolio.work_registered:"
                    f"{ITEM_ID}"),
            }],
        }]}

    monkeypatch.setattr(
        project_discovery, "discover_portfolio_opportunities", _discover)


def _lane(database):
    from jarvis_foundry.executive.autonomy import (
        _run_discovery_starvation_fallback,
    )

    return _run_discovery_starvation_fallback(
        database, cycle_id=CYCLE_ID, fallback_oid=OBJECTIVE_ID,
        capability="write_registered_worktree_files", controller="supervisor",
        source_id="test")


def _item(database):
    return rebuild_portfolio_state(database).work_items[ITEM_ID]


def test_lane_consumes_existing_open_opportunity(
        tmp_path: Path, monkeypatch) -> None:
    """The live defect: a duplicate feed must not hide an OPEN item.

    Before the repair this returns ``(None, None)`` -- the lane exits before
    the selector -- even though the scoped pool holds one eligible,
    resource-bound, authority-aligned opportunity.
    """
    database = _database(tmp_path)
    _open_item(database)
    _refusing_feed(monkeypatch)

    payload, result = _lane(database)

    assert payload is not None, (
        "an already-registered, OPEN, consumable opportunity must still be "
        "selected: the feed's honest duplicate refusal is not a lane verdict")
    assert result is not None
    assert result.get("materialized") is True, (
        f"materialization blocked: {result.get('blocked_reason')}")
    assert result.get("mission_id")


def test_lane_materialization_marks_the_item_consumed(
        tmp_path: Path, monkeypatch) -> None:
    """Consumption is real: the item leaves OPEN, so the lane cannot reselect.

    This is the loop bound.  A second cycle finds an empty scoped pool and
    WAITs instead of re-selecting and re-materializing the same work.
    """
    database = _database(tmp_path)
    _open_item(database)
    _refusing_feed(monkeypatch)

    _payload, result = _lane(database)
    assert result.get("materialized") is True
    assert _item(database).status == "CONSUMED"

    second = _run_second_cycle(database, monkeypatch)
    assert second == (None, None), (
        "a consumed opportunity must not be selected again: "
        f"{second}")


def _run_second_cycle(database, monkeypatch):
    from jarvis_foundry.executive.autonomy import (
        _run_discovery_starvation_fallback,
    )

    _refusing_feed(monkeypatch)
    return _run_discovery_starvation_fallback(
        database, cycle_id="continuous:probe-consume-2",
        fallback_oid=OBJECTIVE_ID,
        capability="write_registered_worktree_files", controller="supervisor",
        source_id="test")


def test_lane_still_refuses_an_item_without_a_resource_binding(
        tmp_path: Path, monkeypatch) -> None:
    """The lane's existing guards are untouched by the repair.

    An item the lane structurally cannot materialize (no exact resource
    binding) is still skipped, and still skipped for the same reason.
    """
    database = _database(tmp_path)
    register_work_item(
        database, item_id=ITEM_ID, kind="REPAIR", objective=OBJECTIVE,
        candidate_id="cand-40b81c46a1bd", resource_id=None,
        expected_value=0.5, urgency=0.4, information_gain=0.5,
        confidence=0.8, effort=0.3, risk=0.2, reversibility=0.9,
        evidence_refs=["github:blemeh/jarvis-foundry@main"],
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "blemeh/jarvis-foundry",
                    "opportunity_kind": "CI_FAILURE",
                    "problem_statement": PROBLEM, "duplicate_key": DUPKEY,
                    "why_now": "still red"},
        metadata={"repository": "blemeh/jarvis-foundry",
                  "executive_objective_id": OBJECTIVE_ID},
        source_id="test",
    )
    _refusing_feed(monkeypatch)

    assert _lane(database) == (None, None)


def test_lane_refuses_work_outside_its_objective_scope(
        tmp_path: Path, monkeypatch) -> None:
    """Scope containment is unchanged: another objective's item is not taken."""
    database = _database(tmp_path)
    register_work_item(
        database, item_id=ITEM_ID, kind="REPAIR", objective=OBJECTIVE,
        candidate_id="cand-40b81c46a1bd", resource_id="resource-1",
        expected_value=0.5, urgency=0.4, information_gain=0.5,
        confidence=0.8, effort=0.3, risk=0.2, reversibility=0.9,
        evidence_refs=["github:blemeh/jarvis-foundry@main"],
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "blemeh/jarvis-foundry",
                    "opportunity_kind": "CI_FAILURE",
                    "problem_statement": PROBLEM, "duplicate_key": DUPKEY,
                    "why_now": "still red"},
        metadata={"repository": "blemeh/jarvis-foundry",
                  "executive_objective_id": "owner-goal:owner-someone-else"},
        source_id="test",
    )
    _refusing_feed(monkeypatch)

    assert _lane(database) == (None, None)


def test_lane_survives_a_failing_discovery_pass(
        tmp_path: Path, monkeypatch) -> None:
    """A discovery failure must not skip work the lane already owns.

    Before the repair a raised feed propagated to the blanket
    ``except Exception: return None, None``.  Discovery is advisory input; its
    failure cannot be evidence that nothing is admissible.
    """
    from jarvis_foundry import project_discovery

    def _boom(*_args, **_kwargs):
        raise RuntimeError("github api unavailable")

    monkeypatch.setattr(
        project_discovery, "discover_portfolio_opportunities", _boom)

    database = _database(tmp_path)
    _open_item(database)

    payload, result = _lane(database)
    assert payload is not None
    assert result.get("materialized") is True, result.get("blocked_reason")


def test_lane_records_an_auditable_refusal_when_nothing_materializes(
        tmp_path: Path, monkeypatch) -> None:
    """A SELECT that cannot materialize leaves a durable receipt.

    The repair moves the admission decision after the selector, so a lane
    SELECT can now be reached in states where materialization still refuses.
    That refusal must be recorded, not returned as a silent no-op.
    """
    import jarvis_foundry.executive.portfolio_bridge as bridge

    database = _database(tmp_path)
    _open_item(database)
    _refusing_feed(monkeypatch)

    # Refuse at the satisfiability gate, which runs before any mission row.

    monkeypatch.setattr(
        bridge, "_discovery_contract_satisfiability_block",
        lambda item, **kwargs: "unsatisfiable: deliverable inside forbidden set")

    payload, result = _lane(database)
    assert payload is not None, "the lane still SELECTed"
    assert result.get("materialized") is False
    assert "unsatisfiable" in str(result.get("blocked_reason") or "")

    receipts = [
        event for event in database.events(
            event_types=("executive.discovery_lane_not_materialized",))
    ]
    assert len(receipts) == 1, receipts
    payload_json = receipts[0].payload
    assert payload_json["item_id"] == ITEM_ID
    assert "unsatisfiable" in payload_json["blocked_reason"]
    assert payload_json["fed_this_pass"] is False, (
        "the receipt must state honestly that discovery fed nothing new")


def test_duplicate_registration_is_still_refused(
        tmp_path: Path, monkeypatch) -> None:
    """Idempotency is preserved: the feed conflict is NOT bypassed.

    The repair consumes an existing item; it must not weaken discovery
    deduplication or fabricate a second candidate for the same opportunity.
    """
    from jarvis_foundry.project_discovery import Opportunity, feed_portfolio

    database = _database(tmp_path)
    _open_item(database)
    opportunity = Opportunity(
        kind="CI_FAILURE", repository="blemeh/jarvis-foundry",
        problem_statement=PROBLEM,
        evidence_refs=("github:blemeh/jarvis-foundry@main",),
        expected_value=0.5, confidence=0.8, effort=0.3, risk=0.2,
        reversibility=0.9, urgency=0.4, information_gain=0.5,
        why_now="still red", duplicate_key=DUPKEY,
    )
    result = feed_portfolio(
        database, "portfolio-discovery:test", [opportunity],
        resource_id="resource-1", executive_objective_id=OBJECTIVE_ID)

    assert "portfolio_error" in result["fed"][0], (
        "a duplicate registration must still be refused, not silently "
        f"admitted: {result['fed'][0]}")
    assert "portfolio_item_id" not in result["fed"][0]
    assert _item(database).status == "OPEN", (
        "the duplicate feed must not mutate the existing item")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))