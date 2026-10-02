"""A reopened recovery item must be SELECTABLE (D5, live 2026-10-02 21:00 UTC).

The bounded recovery reopen (93a396c) correctly flipped the live item
`pw-dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92` from BLOCKED to
OPEN at 21:00:28Z and re-linked the new candidate.  Selection then WAITed
with:

    rejected reason -> "blocked:recovery reopen for cand-40b81c46a1bd:
                        terminal lineage failed, budget allows one
                        re-derivation"

First broken edge: ``set_work_status`` stamps ``blocked_reason = payload
["reason"]`` for EVERY status transition, and ``_capacity_check`` treats ANY
non-empty ``blocked_reason`` as a live block.  So the item's own recovery
NARRATIVE — the text explaining why it was reopened — permanently
disqualified the reopened item.  The gate can never be satisfied by the
reopen path that wrote it: recovery was structurally unreachable while
reading truthfully.

The gate must keep its meaning (a genuinely blocked item stays ineligible)
while ignoring the narrative of the transition that made the item
executable again.

These tests are authored BEFORE the repair and must fail on 639ec57.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.evolution.executive_portfolio import (
    rebuild_portfolio_state,
    register_work_item,
    select_next_work,
    set_work_status,
)

from worker_test_helpers import make_repository


def _database(tmp_path: Path):
    """A bare initialized database with the registered git resource.

    ``mission_database`` seeds an ACTIVE mission, which consumes the default
    ``max_concurrent_missions == 1`` capacity and would mask the gate under
    test with ``capacity:concurrent_missions``.  The registered resource is
    required because discovery-opportunity materialization fails closed on an
    unregistered binding — which is exactly the live registry shape.
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

PROBLEM = ("fix failing CI on blemeh/jarvis-foundry: workflow "
           "'.github/workflows/windows-ci.yml' concluded failure at "
           "2026-08-19T03:42:59Z; changes currently land without CI "
           "verification")
OBJECTIVE = f"CI_FAILURE: {PROBLEM}"
DUPKEY = "dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92"
ITEM_ID = f"pw-{DUPKEY}"


def _register(database, *, objective_id: str | None = None) -> None:
    metadata: dict[str, object] = {"repository": "blemeh/jarvis-foundry"}
    if objective_id:
        metadata["executive_objective_id"] = objective_id
    register_work_item(
        database, item_id=ITEM_ID, kind="REPAIR", objective=OBJECTIVE,
        candidate_id="cand-old", resource_id="resource-1",
        expected_value=0.5, urgency=0.4, information_gain=0.5,
        confidence=0.8, effort=0.3, risk=0.2, reversibility=0.9,
        evidence_refs=["github:blemeh/jarvis-foundry@main"],
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "blemeh/jarvis-foundry",
                    "opportunity_kind": "CI_FAILURE",
                    "problem_statement": PROBLEM, "duplicate_key": DUPKEY,
                    "why_now": "CI failure observed"},
        metadata=metadata, source_id="test",
    )


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
    for index, state in enumerate(["ORIENTING", "ACTION_SELECTION", "READY",
                                   "EXECUTING", "BLOCKED"]):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state,
             "next_action": "execute"},
            source_type="supervisor", source_id="test",
            idempotency_key=f"mission.state_changed:{mission_id}:{state}:{index}",
        ))


def _item(database):
    return rebuild_portfolio_state(database).work_items[ITEM_ID]


def _pool_item_ids(database, item_id: str) -> list[str]:
    """Item ids the selector actually considers, via the real pool builder."""
    from jarvis_foundry.evolution.executive_portfolio import _all_work_items

    state = rebuild_portfolio_state(database)
    return [
        item.item_id for item in _all_work_items(
            database, state, resource_id=None,
            capability="write_registered_worktree_files",
            candidate_limit=20, goals=[], now=None, objective_id=None)
        if item.item_id == item_id
    ]


def _reopened(database, *, objective_id: str | None = None) -> None:
    """Reproduce the exact live sequence: dead mission → bounded reopen."""
    from jarvis_foundry.project_discovery import (
        Opportunity,
        feed_portfolio,
    )

    _register(database, objective_id=objective_id)
    set_work_status(database, ITEM_ID, "CONSUMED",
                    reason="materialized:mission-old", source_id="test")
    set_work_status(database, ITEM_ID, "BLOCKED",
                    reason="mission mission-old terminal: BLOCKED",
                    source_id="test")
    _failed_mission(database, mission_id="mission-old")
    opportunity = Opportunity(
        kind="CI_FAILURE", repository="blemeh/jarvis-foundry",
        problem_statement=PROBLEM,
        evidence_refs=("github:blemeh/jarvis-foundry@main",),
        expected_value=0.5, confidence=0.8, effort=0.3, risk=0.2,
        reversibility=0.9, urgency=0.4, information_gain=0.5,
        why_now="CI failure still present on the branch",
        duplicate_key=DUPKEY,
    )
    result = feed_portfolio(database, "portfolio-discovery:test",
                            [opportunity], resource_id="resource-1",
                            executive_objective_id=objective_id)
    assert "portfolio_error" not in result["fed"][0], (
        f"reopen refused: {result['fed'][0].get('portfolio_error')}")


OBJECTIVE_ID = "owner-goal:owner-c4dbe49e2dd7b49e11583655"


def test_reopened_recovery_item_is_selectable(tmp_path: Path) -> None:
    """The live defect: an OPEN, reopened item must not read as blocked.

    Before the repair this returns ``kind == "WAIT"`` with rejection reason
    ``blocked:recovery reopen for ...`` — the item's own reopen narrative
    disqualified it forever.

    Selection is objective-scoped, exactly as the live discovery-fed path
    calls it (``select_next_work(objective_id=...)``), so the comparison is
    the one production makes.
    """
    database, _repo = _database(tmp_path), None
    _reopened(database, objective_id=OBJECTIVE_ID)

    item = _item(database)
    assert item.status == "OPEN"

    decision = select_next_work(database, loop_id="probe:select-after-reopen",
                                source_id="test", objective_id=OBJECTIVE_ID)
    rejection_reasons = (
        list(decision.capacity_check.get("rejection_reasons") or [])
        if isinstance(decision.capacity_check, dict) else [])
    blocked_by_own_narrative = [
        reason for reason in rejection_reasons
        if reason.startswith("blocked:")
    ]
    assert not blocked_by_own_narrative, (
        "reopened item disqualified by its own reopen narrative: "
        f"{blocked_by_own_narrative}")
    assert decision.kind == "SELECT", (
        f"reopened recovery item must be selectable, got {decision.kind}: "
        f"{rejection_reasons}")
    assert decision.entity_id == ITEM_ID


def test_reopened_item_survives_materialization(tmp_path: Path) -> None:
    """The whole point of the repair: the mission actually materializes."""
    from jarvis_foundry.executive.portfolio_bridge import (
        materialize_portfolio_selection,
    )

    database, _repo = _database(tmp_path), None
    _reopened(database, objective_id=OBJECTIVE_ID)
    decision = select_next_work(database, loop_id="probe:materialize",
                                source_id="test", objective_id=OBJECTIVE_ID)
    assert decision.kind == "SELECT", decision.to_payload()

    result = materialize_portfolio_selection(
        database, decision=decision.to_payload(), resource_id="resource-1",
        capability="write_registered_worktree_files", source_id="test")
    assert result["materialized"] is True, (
        f"materialization blocked: {result.get('blocked_reason')}")
    assert _item(database).status == "CONSUMED"


def test_reopen_narrative_stays_in_the_record(tmp_path: Path) -> None:
    """The repair must not erase the reason: it stops GATING on it."""
    database, _repo = _database(tmp_path), None
    _reopened(database)
    item = _item(database)
    assert "recovery reopen" in str(item.blocked_reason or ""), (
        "the audit narrative must remain queryable")
    assert item.status == "OPEN"


def test_genuinely_blocked_item_stays_ineligible(tmp_path: Path) -> None:
    """A genuinely BLOCKED item is never admitted to the ranked pool.

    The enforcing gate is ``_all_work_items`` (which admits only
    OPEN/PLANNED/ELIGIBLE registered items), NOT the ``_capacity_check``
    clause changed by the D5 repair — that clause is unreachable for
    registered items.  Asserted here so the invariant is pinned to its real
    enforcement point rather than misattributed to the repaired line.
    """
    database, _repo = _database(tmp_path), None
    _register(database)
    set_work_status(database, ITEM_ID, "BLOCKED",
                    reason="waiting for owner decision on vendor choice",
                    source_id="test")

    decision = select_next_work(database, loop_id="probe:real-block",
                                source_id="test")
    assert decision.kind == "WAIT"
    assert _item(database).status == "BLOCKED"
    assert not _pool_item_ids(database, ITEM_ID), (
        "a BLOCKED item must not be admitted to the ranked pool")


def test_terminal_status_alone_still_rejects(tmp_path: Path) -> None:
    """Terminal statuses reject on status, not on the narrative."""
    database, _repo = _database(tmp_path), None
    _register(database)
    set_work_status(database, ITEM_ID, "CANCELLED",
                    reason="owner cancelled this line of work",
                    source_id="test")

    decision = select_next_work(database, loop_id="probe:cancelled",
                                source_id="test")
    assert decision.kind == "WAIT"
    assert not _pool_item_ids(database, ITEM_ID)


def test_open_status_with_narrative_is_admitted(tmp_path: Path) -> None:
    """An OPEN item carrying any leftover narrative enters the ranked pool."""
    database, _repo = _database(tmp_path), None
    _register(database)
    set_work_status(database, ITEM_ID, "OPEN",
                    reason="recovery reopen for cand-x: terminal lineage",
                    source_id="test")

    decision = select_next_work(database, loop_id="probe:open-with-narrative",
                                source_id="test")
    assert decision.kind == "SELECT", decision.to_payload()


def test_status_transitions_are_idempotent_per_cycle_not_forever(
        tmp_path: Path) -> None:
    """A status transition must be re-recordable for a NEW cycle.

    Second live defect on the same chain, proven by the 2026-09-29 ->
    2026-10-02 lineage: `set_work_status` keys its event by
    ``{item_id}:{status}``, so the FIRST ever CONSUMED/BLOCKED for an item
    burned that key permanently.  A bounded recovery that reopens the item
    and materializes it again then collides with its own first cycle:

        portfolio.work_status_changed:pw-dupkey-...:CONSUMED
        -> idempotency conflict ... (portfolio_bridge, consumption stamp)

    The stamp is guarded fail-closed, so the retryable recovery mission could
    never be materialized at all.  Exactly-once is a property of one logical
    transition, not of a status value for all time: the key must carry the
    transition's own identity (its reason), so a repeat of the SAME transition
    is still a no-op while a genuinely new one records.
    """
    database, _repo = _database(tmp_path), None
    _register(database)

    first = set_work_status(database, ITEM_ID, "CONSUMED",
                            reason="materialized:mission-1", source_id="test")
    assert first

    # Same transition again: exactly-once preserved, no duplicate effect.
    repeat = set_work_status(database, ITEM_ID, "CONSUMED",
                             reason="materialized:mission-1", source_id="test")
    assert repeat == first

    # A NEW materialization of the same status must be recordable.
    again = set_work_status(database, ITEM_ID, "CONSUMED",
                            reason="materialized:mission-2", source_id="test")
    assert again != first, (
        "a second, genuinely new CONSUMED transition must not collide with "
        "the first cycle's key")
    assert _item(database).status == "CONSUMED"


def test_terminal_mission_repair_item_is_bounded(tmp_path: Path) -> None:
    """P1-4: a derived REPAIR item must NOT re-select on every cycle.

    ``_mission_items`` derives ``repair:{mission_id}`` (kind=REPAIR,
    status=OPEN) for every terminal mission, forever.  The pre-D5 narrative
    gate was the only thing rejecting it: its blocked_reason was non-empty,
    so it never entered the ranked pool.

    D5 removed that gate, so with both repairs applied the item is SELECTed
    on every single cycle, materializes nothing (the mission has no
    executable step contract left), and appends a fresh
    ``portfolio.repair_retired`` each time because that event is keyed by
    ``{decision_id}:{mission_id}`` and decision_id changes per cycle.  The
    ledger grows without bound and ``repair_budget_exhausted`` never fires,
    because ``repair_attempts`` is never incremented anywhere.

    A repair item whose owning mission is terminal must retire itself from
    the pool once its materialization has been attempted and retired, rather
    than being re-admitted forever.
    """
    from jarvis_foundry.evolution.executive_portfolio import (
        _all_work_items,
    )

    database, _repo = _database(tmp_path), None
    _failed_mission(database, mission_id="mission-terminal")

    def repair_ids() -> list[str]:
        state = rebuild_portfolio_state(database)
        return [
            item.item_id for item in _all_work_items(
                database, state, resource_id=None,
                capability="write_registered_worktree_files",
                candidate_limit=20, goals=[],
                now=None, objective_id=None)
            if item.kind == "REPAIR"
        ]

    assert repair_ids() == ["repair:mission-terminal"], repair_ids()

    # A portfolio.repair_retired event for this mission must stop it being
    # re-admitted, exactly once, durably.
    from jarvis_foundry.events import Event
    database.append(Event.create(
        "portfolio.repair_retired",
        {"decision_id": "probe:dec-1", "item_id": "repair:mission-terminal",
         "kind": "REPAIR", "mission_id": "mission-terminal",
         "reason": "no remaining executable step contract"},
        source_type="supervisor", source_id="test",
        idempotency_key="portfolio.repair_retired:probe:dec-1:mission-terminal"))

    assert repair_ids() == [], (
        "a retired repair item must not be re-admitted to the ranked pool; "
        f"got {repair_ids()}")