"""FAILED-status recovery plus the bounded release-scoped window.

Live 2026-10-04, ``mission-4646c7023b05``: three correct worker attempts
were condemned by ``evidence_mismatch`` (the containment defect this branch
also repairs).  Its portfolio item, ``pw-dupkey-blemeh/jarvis-foundry-
CI_FAILURE-f2757e1b6f92``, therefore carries status ``FAILED`` while the
objective's ledger holds TWO terminal failures (``mission-ecdb7918c2d4``
BLOCKED 2026-09-30, ``mission-4646c7023b05`` 2026-10-04) -- the original
recovery allowance was already spent by the 2026-10-02 reopen.

Two defects blocked the honest recovery path, and both are exercised here:

1. ``_reopen_terminal_item_for_recovery`` accepted only BLOCKED/CANCELLED,
   so the product's own ``FAILED`` item could never reopen even when the
   duplicate-coverage release admitted the objective -- the two gates
   disagreeing on one ledger.
2. The release-scoped window was unbounded in time: every later release
   would have reopened the same ineffective strategy forever.  It is now
   grantable exactly ONCE per objective (the second terminal failure), and
   only when the live release moved past every terminal failure.

Every fixture uses the production APIs (``generate_candidate`` /
``feed_portfolio`` / ``register_work_item`` / ``set_work_status`` /
``release.slot_changed``) so the projections under test are the live ones.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import (
    canonicalize_statement,
    covered_objective,
    recovery_window_open,
    recovery_window_state,
)
from jarvis_foundry.evolution.executive_portfolio import (
    rebuild_portfolio_state,
    register_work_item,
    set_work_status,
)
from jarvis_foundry.events import Event
from jarvis_foundry.project_discovery import Opportunity, feed_portfolio

from worker_test_helpers import mission_database

PROBLEM = ("fix failing CI on blemeh/jarvis-foundry: workflow "
           "'.github/workflows/windows-ci.yml' concluded failure at "
           "2026-08-19T03:42:59Z; changes currently land without CI "
           "verification")
OBJECTIVE = f"CI_FAILURE: {PROBLEM}"
DUPKEY = "dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92"
ITEM_ID = f"pw-{DUPKEY}"
CANONICAL = canonicalize_statement(OBJECTIVE)

# Timestamps used to place terminal failures relative to release floors.
BEFORE_RELEASE = "2026-09-01T00:00:00.000000Z"
RELEASE_ONE = "2026-10-04T00:00:00.000000Z"
AFTER_RELEASE_ONE = "2026-10-04T12:00:00.000000Z"
RELEASE_TWO = "2026-10-05T00:00:00.000000Z"


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


def _register_item(database, *, status: str = "FAILED",
                   candidate_id: str = "cand-dead") -> None:
    """The live end state: registered item whose owning mission is terminal."""
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
        metadata={"repository": "blemeh/jarvis-foundry"},
        source_id="test",
    )
    if status != "OPEN":
        set_work_status(database, ITEM_ID, status,
                        reason=f"mission mission-dead terminal: {status}",
                        source_id="test")


def _walk_states(terminal: str) -> list[str]:
    """A legal mission walk ending in ``terminal`` (``domain`` transitions)."""
    if terminal == "COMPLETE":
        return ["ORIENTING", "ACTION_SELECTION", "COMPLETE"]
    if terminal == "EXECUTING":
        return ["ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING"]
    return ["ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING", terminal]


def _failed_mission(database, *, mission_id: str,
                    terminal: str = "BLOCKED",
                    at: str | None = None) -> None:
    """A terminally-failed lineage, optionally back-dated for floor tests."""
    def _stamp(event: Event) -> Event:
        return event if at is None else replace(event, recorded_at=at)

    database.append(_stamp(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": OBJECTIVE,
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["green"], "next_action": "execute",
         "created_from": f"portfolio:{ITEM_ID}"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"mission.created:{mission_id}",
    )))
    for index, state in enumerate(_walk_states(terminal)):
        database.append(_stamp(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state,
             "next_action": None if state == "COMPLETE" else "execute"},
            source_type="supervisor", source_id="test",
            idempotency_key=(
                f"mission.state_changed:{mission_id}:{state}:{index}"),
        )))


def _seed_candidate(database) -> str:
    """Ensure a live NEW/SCORED candidate exists for the objective.

    When a COMPLETE or running mission already covers the objective the
    generation is honestly marked ``duplicate_of`` -- the candidate row is
    still recorded, which is all the coverage lookup needs.
    """
    from jarvis_foundry.discovery_candidates import (
        generate_candidate,
        score_candidate,
    )

    result = generate_candidate(database, "mission-seed", "repo", OBJECTIVE)
    score_candidate(database, result["candidate_id"])
    return str(result["candidate_id"])


def _activate_release(database, release_id: str, *, recorded_at: str) -> None:
    """Move the live release slot the way production does.

    ``registry.register_release`` refuses without a promotion-eligible
    ``improvement_records`` row, which a focused window test has no reason
    to build, so the manifest is a fixture payload (the projection
    validates its shape).  The SLOT transition is not a fixture: it carries
    ``registry._slot_key``'s idempotency key and the same
    ``previous == release_id`` no-op guard ``set_slot`` applies, so a
    re-assert can never move the release floor.
    """
    with database.connect() as connection:
        row = connection.execute(
            "SELECT release_id FROM release_slots WHERE slot = 'live'"
        ).fetchone()
    previous = str(row[0]) if row and row[0] else None
    if previous == release_id:
        return
    manifest = Event.create(
        "release.manifest_recorded",
        {"release_id": release_id,
         "source_commit": "a" * 40,
         "artifact_hashes": {"RELEASE.json": "b" * 64},
         "dependencies": [],
         "runtime_identity": {"schema": 41},
         "parent_release_id": None,
         "improvement_id": f"deployment:{release_id}",
         "risk_class": "R0",
         "migrations": [41],
         "verdicts": [],
         "rollback_target": None},
        source_type="supervisor", source_id="test-release",
        idempotency_key=f"release.manifest_recorded:{release_id}",
    )
    slot = Event.create(
        "release.slot_changed",
        {"slot": "live", "release_id": release_id,
         "previous_release_id": previous,
         "reason": "test activation", "controller": "supervisor"},
        source_type="supervisor", source_id="test-release",
        idempotency_key=(
            f"release.slot_changed:live:{release_id or 'clear'}:"
            f"{previous or 'none'}"),
    )
    database.append_events([
        replace(manifest, recorded_at=recorded_at),
        replace(slot, recorded_at=recorded_at),
    ])


def _window(database):
    with database.connect() as connection:
        return recovery_window_state(connection, CANONICAL)


def _is_open(database) -> bool:
    with database.connect() as connection:
        return recovery_window_open(connection, CANONICAL)


def _covered(database):
    return covered_objective(database, OBJECTIVE)


def _feed(database):
    result = feed_portfolio(database, "portfolio-discovery:test",
                            [_opportunity()], resource_id="resource-1")
    assert len(result["fed"]) == 1
    return result["fed"][0]


def _item(database):
    return rebuild_portfolio_state(database).work_items[ITEM_ID]


def _reopen_events(database) -> list[str]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'portfolio.work_status_changed' ORDER BY sequence").fetchall()
    found = []
    for row in rows:
        payload = str(row[0])
        if (f'"item_id":"{ITEM_ID}"' in payload
                and '"status":"OPEN"' in payload
                and "recovery reopen" in payload):
            found.append(payload)
    return found


# --------------------------------------------------------------------------
# 1 + 2: FAILED reopens; BLOCKED/CANCELLED and live statuses stay correct
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["FAILED", "BLOCKED", "CANCELLED"])
def test_terminal_item_status_reopens_for_recovery(
        tmp_path: Path, status: str) -> None:
    """FAILED is the product's own terminal status and must reopen too."""
    database, _repo = mission_database(tmp_path)
    _register_item(database, status=status)
    _failed_mission(database, mission_id="mission-old")

    entry = _feed(database)

    assert "portfolio_error" not in entry, (
        f"{status} item must reopen: {entry.get('portfolio_error')}")
    assert entry.get("recovered") is not None, (
        f"{status} item did not produce a recovery receipt")
    assert entry["recovered"]["previous_status"] == status
    assert entry["recovered"]["failed_lineages"] == 1
    assert _item(database).status == "OPEN"
    assert _item(database).candidate_id != "cand-dead", (
        "reopen must link the NEW candidate")


@pytest.mark.parametrize("status", ["OPEN", "CONSUMED", "PLANNED",
                                    "ELIGIBLE"])
def test_live_item_status_never_reopens(tmp_path: Path, status: str) -> None:
    """A live item keeps the honest conflict: something else may work it."""
    database, _repo = mission_database(tmp_path)
    _register_item(database, status=status)
    _failed_mission(database, mission_id="mission-old")

    entry = _feed(database)

    assert entry.get("recovered") is None, (
        f"a {status} item must not be reopened")
    assert _item(database).status == status


# --------------------------------------------------------------------------
# 3: the original recovery allowance is preserved
# --------------------------------------------------------------------------

def test_original_allowance_survives_without_release_evidence(
        tmp_path: Path) -> None:
    """One terminal failure still releases coverage with no release row."""
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _seed_candidate(database)

    assert _window(database).open is True
    assert _is_open(database) is True
    assert _covered(database) is None


def test_original_allowance_survives_a_release_that_predates_the_failure(
        tmp_path: Path) -> None:
    """The one-recovery bound never needed a release transition."""
    database, _repo = mission_database(tmp_path)
    _activate_release(database, "rel-predecessor-00000001",
                      recorded_at="2026-01-01T00:00:00.000000Z")
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _seed_candidate(database)

    window = _window(database)
    assert window.release_floor is not None
    assert window.failed == 1
    assert window.open is True
    assert _covered(database) is None


# --------------------------------------------------------------------------
# 4: a relevant release/repair transition permits ONE bounded reconsideration
# --------------------------------------------------------------------------

def test_release_still_pinned_to_the_failing_contract_stays_closed(
        tmp_path: Path) -> None:
    """Both failures happened under the CURRENT release: no window."""
    database, _repo = mission_database(tmp_path)
    _activate_release(database, "rel-predecessor-00000001",
                      recorded_at=BEFORE_RELEASE)
    _register_item(database)
    _failed_mission(database, mission_id="mission-ecdb7918c2d4")
    _failed_mission(database, mission_id="mission-4646c7023b05")
    _seed_candidate(database)

    window = _window(database)
    assert window.failed == 2
    assert window.failed_since_release == 2
    assert window.open is False
    assert _covered(database) is not None

    entry = _feed(database)
    assert entry.get("recovered") is None
    assert _item(database).status == "FAILED"


def test_release_transition_after_every_failure_opens_one_window(
        tmp_path: Path) -> None:
    """The repaired contract is a genuinely changed condition -- once."""
    database, _repo = mission_database(tmp_path)
    _activate_release(database, "rel-predecessor-00000001",
                      recorded_at=BEFORE_RELEASE)
    _register_item(database)
    _failed_mission(database, mission_id="mission-ecdb7918c2d4",
                    at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-4646c7023b05",
                    at=BEFORE_RELEASE)
    _seed_candidate(database)
    assert _window(database).open is False, (
        "a release that predates the failures proves nothing")

    _activate_release(database, "rel-repaired-evidence-00000002",
                      recorded_at=RELEASE_ONE)

    window = _window(database)
    assert window.failed == 2
    assert window.failed_since_release == 0
    assert window.open is True
    assert _covered(database) is None

    entry = _feed(database)
    assert entry.get("recovered") is not None, (
        f"the release-scoped window must reopen: "
        f"{entry.get('portfolio_error')}")
    assert entry["recovered"]["failed_lineages"] == 2
    assert entry["recovered"]["failed_since_release"] == 0
    assert _item(database).status == "OPEN"


def test_live_ledger_shape_stays_closed_until_this_repair_ships(
        tmp_path: Path) -> None:
    """The exact 2026-10-04 ledger, and the shape promotion must produce.

    Live: floor ``rel-99661a863d67-fa132ae4`` activated
    2026-10-03T23:53:29Z; ``mission-ecdb7918c2d4`` terminally failed
    2026-09-30 (BEFORE the floor) and ``mission-4646c7023b05`` 2026-10-04
    T00:05:07Z (AFTER it).  So ``failed == 2`` with
    ``failed_since_release == 1``: the current release already had its
    chance and must NOT re-open.  The window opens only once the repaired
    release becomes live -- which is exactly what promoting this candidate
    does.
    """
    database, _repo = mission_database(tmp_path)
    _activate_release(database, "rel-99661a863d67-fa132ae4",
                      recorded_at="2026-10-03T23:53:29.253860Z")
    _register_item(database)
    _failed_mission(database, mission_id="mission-ecdb7918c2d4",
                    at="2026-09-30T20:40:33.605902Z")
    _failed_mission(database, mission_id="mission-4646c7023b05",
                    at="2026-10-04T00:05:07.224839Z")
    _seed_candidate(database)

    window = _window(database)
    assert window.release_floor == "2026-10-03T23:53:29.253860Z"
    assert window.failed == 2
    assert window.failed_since_release == 1
    assert window.open is False, (
        "the release that produced a failure must not re-authorize it")
    assert _covered(database) is not None
    assert _feed(database).get("recovered") is None

    _activate_release(database, "rel-autonomy-recovery-evidence-00000002",
                      recorded_at="2026-10-05T00:00:00.000000Z")

    window = _window(database)
    assert window.failed_since_release == 0
    assert window.open is True, (
        "the repaired release is a genuinely changed executable contract")
    assert _covered(database) is None
    entry = _feed(database)
    assert entry.get("recovered") is not None, (
        f"post-promotion the objective must be recoverable: "
        f"{entry.get('portfolio_error')}")
    assert entry["recovered"]["failed_lineages"] == 2
    assert _item(database).status == "OPEN"


def test_reasserting_the_live_slot_does_not_move_the_release_floor(
        tmp_path: Path) -> None:
    """``set_slot``'s no-op guard keeps a re-assert from buying a window."""
    from jarvis_foundry.evolution.registry import set_slot

    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-one", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-two", at=BEFORE_RELEASE)
    _seed_candidate(database)
    _activate_release(database, "rel-stable-00000001",
                      recorded_at=RELEASE_ONE)
    assert _window(database).open is True

    set_slot(database, "live", "rel-stable-00000001",
             reason="re-assert the same live release",
             controller="supervisor", source_id="test")

    window = _window(database)
    assert window.release_floor == RELEASE_ONE, (
        "re-pointing the slot at the release it already holds must not "
        f"move the floor: {window.release_floor}")
    assert window.failed_since_release == 0
    assert window.open is True, (
        "the floor did not move, so the window count is unchanged")


# --------------------------------------------------------------------------
# 5 + 6: repeated checks consume nothing; a second failure consumes the window
# --------------------------------------------------------------------------

def test_repeated_checks_cannot_create_further_attempts(
        tmp_path: Path) -> None:
    """Same ledger, same answer -- and only ONE recovery is ever recorded."""
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")

    for _ in range(3):
        assert _is_open(database) is True

    first = _feed(database)
    assert first.get("recovered") is not None
    assert _item(database).status == "OPEN"

    second = _feed(database)
    assert second.get("recovered") is None, (
        "an OPEN item must not be reopened a second time")
    assert "portfolio_error" in second, (
        "the second feed must fall back to the honest conflict")
    assert _item(database).status == "OPEN"

    assert len(_reopen_events(database)) == 1, (
        "repeated checks under identical conditions must not append a "
        "second recovery transition")


def test_second_failure_consumes_the_release_scoped_window(
        tmp_path: Path) -> None:
    """The release-scoped attempt fails -> the objective re-covers for good."""
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-ecdb7918c2d4",
                    at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-4646c7023b05",
                    at=BEFORE_RELEASE)
    _activate_release(database, "rel-repaired-evidence-00000002",
                      recorded_at=RELEASE_ONE)
    assert _window(database).open is True

    # The release-scoped attempt ran and failed under that release.
    _failed_mission(database, mission_id="mission-third",
                    at=AFTER_RELEASE_ONE)
    _seed_candidate(database)

    window = _window(database)
    assert window.failed == 3
    assert window.failed_since_release == 1
    assert window.open is False, (
        "a failure under the release that opened the window consumes it")
    assert _covered(database) is not None

    entry = _feed(database)
    assert entry.get("recovered") is None
    assert _item(database).status == "FAILED"


def test_unrelated_later_release_cannot_restart_the_cycle(
        tmp_path: Path) -> None:
    """A third terminal failure is permanent, however many releases follow."""
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-one", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-two", at=BEFORE_RELEASE)
    # The release-scoped window opens, and the attempt it buys fails.
    _activate_release(database, "rel-repaired-evidence-00000002",
                      recorded_at=RELEASE_ONE)
    _failed_mission(database, mission_id="mission-three",
                    at=AFTER_RELEASE_ONE)
    _seed_candidate(database)
    assert _window(database).open is False

    _activate_release(database, "rel-unrelated-docs-00000003",
                      recorded_at=RELEASE_TWO)

    window = _window(database)
    assert window.failed_since_release == 0, (
        "the new release does post-date every failure")
    assert window.failed == 3
    assert window.open is False, (
        "an unrelated release must not buy a third attempt")
    assert _covered(database) is not None

    entry = _feed(database)
    assert entry.get("recovered") is None
    assert _item(database).status == "FAILED"


# --------------------------------------------------------------------------
# 8: a completed or currently live mission prevents duplicate work
# --------------------------------------------------------------------------

def test_completed_mission_blocks_duplicate_work(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _failed_mission(database, mission_id="mission-done", terminal="COMPLETE")
    _seed_candidate(database)

    assert _covered(database) == "mission-done"

    entry = _feed(database)
    assert entry.get("recovered") is None
    assert entry.get("duplicate_of") == "mission-done"
    assert _item(database).status == "FAILED"


def test_live_mission_blocks_duplicate_work(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _failed_mission(database, mission_id="mission-running",
                    terminal="EXECUTING")
    _seed_candidate(database)

    assert _covered(database) == "mission-running"

    entry = _feed(database)
    assert entry.get("recovered") is None
    assert entry.get("duplicate_of") == "mission-running"
    assert _item(database).status == "FAILED"


# --------------------------------------------------------------------------
# 9: both gates make consistent decisions
# --------------------------------------------------------------------------

def _shape_one_failure(database) -> None:
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _seed_candidate(database)


def _shape_two_failures_current_release(database) -> None:
    _activate_release(database, "rel-predecessor-00000001",
                      recorded_at=BEFORE_RELEASE)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-recovery",
                    at=BEFORE_RELEASE)
    _seed_candidate(database)


def _shape_two_failures_new_release(database) -> None:
    _register_item(database)
    _failed_mission(database, mission_id="mission-old", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-recovery",
                    at=BEFORE_RELEASE)
    _activate_release(database, "rel-repaired-evidence-00000002",
                      recorded_at=RELEASE_ONE)
    _seed_candidate(database)


def _shape_three_failures(database) -> None:
    _register_item(database)
    _failed_mission(database, mission_id="mission-one", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-two", at=BEFORE_RELEASE)
    _failed_mission(database, mission_id="mission-three",
                    at=AFTER_RELEASE_ONE)
    _seed_candidate(database)


def _shape_complete_mission(database) -> None:
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _failed_mission(database, mission_id="mission-done", terminal="COMPLETE")
    _seed_candidate(database)


def _shape_live_mission(database) -> None:
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    _failed_mission(database, mission_id="mission-running",
                    terminal="EXECUTING")
    _seed_candidate(database)


@pytest.mark.parametrize("shape,window_is_coverage", [
    (_shape_one_failure, True),
    (_shape_two_failures_current_release, True),
    (_shape_two_failures_new_release, True),
    (_shape_three_failures, True),
    # Finished/running work holds coverage for a reason no recovery gate
    # disputes, so the shared window may be open while coverage is NOT
    # released -- but the reopen must still never fire, because the feed
    # never reaches it.
    (_shape_complete_mission, False),
    (_shape_live_mission, False),
], ids=lambda value: (value.__name__.removeprefix("_shape_")
                      if callable(value) else str(value)))
def test_both_gates_agree_on_every_ledger_shape(
        tmp_path: Path, shape, window_is_coverage: bool) -> None:
    """Duplicate-coverage release and the terminal-item reopen agree.

    The invariant ``feed_portfolio`` actually depends on: a recovery
    receipt is produced if and only if the canonical objective is NOT
    already covered.  A gate that releases coverage while the reopen
    refuses (or vice versa) strands the opportunity behind an honest
    conflict forever.
    """
    database, _repo = mission_database(tmp_path)
    shape(database)

    covered = _covered(database)
    window = _window(database)
    entry = _feed(database)
    reopened = entry.get("recovered") is not None

    assert reopened == (covered is None), (
        f"gates disagree: covered={covered!r}, window.open={window.open!r}, "
        f"reopened={reopened}, entry={entry}")
    if window_is_coverage:
        assert window.open == (covered is None), (
            f"the shared window must equal the coverage decision: "
            f"covered={covered!r}, window={window!r}")


# --------------------------------------------------------------------------
# 10: replay / restart preserves eligibility and consumption
# --------------------------------------------------------------------------

def test_restart_preserves_the_same_eligibility_and_consumption(
        tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _register_item(database)
    _failed_mission(database, mission_id="mission-old")
    path = database.path

    before = _window(database)
    assert before.open is True

    restarted = FoundryDatabase(path)
    after = _window(restarted)
    assert after == before, (
        "a restart must re-derive the identical recovery state")
    assert _is_open(restarted) is True

    entry = _feed(restarted)
    assert entry.get("recovered") is not None
    assert entry["recovered"]["failed_lineages"] == before.failed
    assert _item(restarted).status == "OPEN"

    replayed = FoundryDatabase(path)
    assert _item(replayed).status == "OPEN"
    assert _window(replayed) == before, (
        "consuming the window must not change the ledger-derived state")

    again = _feed(replayed)
    assert again.get("recovered") is None
    assert _item(replayed).status == "OPEN"
    assert len(_reopen_events(replayed)) == 1
