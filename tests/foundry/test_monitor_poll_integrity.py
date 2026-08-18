"""V4-S5 round-2 (verifier FAIL findings): poll integrity.

deleg_71efdc9e FAIL findings, repaired:
- HIGH P6c: repeated polls/cycles must NEVER increase selectable
  candidates (duplicate rows landed as selectable NEW; duplicate
  missions with identical objectives were created);
- MEDIUM: queue-REJECTED low-value items must never resurrect via the
  poll feed;
- LOW: discovery.monitor_polled for an unknown monitor must fail the
  append atomically; empty-objective items must not fabricate;
- INFO: crash-retried polls dedup on a stable key; monitors has a
  UNIQUE(source_kind, source_ref) index (migration v18).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate
from jarvis_foundry.discovery_monitors import (
    poll_monitor,
    register_monitor,
)
from jarvis_foundry.events import Event
from jarvis_foundry.v3_loop import _top_scorable


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def selectable(db: FoundryDatabase) -> list[str]:
    return [c["candidate_id"] for c in _top_scorable(db, budget=200)]


def test_repeated_polls_never_increase_candidates(db):
    """The verifier's P6c probe as a regression: two identical polls of
    an uncovered objective create ONE candidate; further identical polls
    create nothing and nothing becomes selectable twice."""
    monitor_id = register_monitor(db, "mission-1", "vault", "root")
    item = {"mission_id": "mission-1", "source": "monitor:vault",
            "objective": "Fix the freeze window in the gateway.",
            "evidence": [{"claim_id": "c1"}]}
    first = poll_monitor(db, monitor_id, [item])
    second = poll_monitor(db, monitor_id, [item])
    third = poll_monitor(db, monitor_id, [item])
    assert first["candidates_found"] == 1
    assert second["candidates_found"] == 0
    assert third["candidates_found"] == 0
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT status, duplicate_of FROM candidates").fetchall()
    assert len(rows) == 1  # the original only — no duplicate rows at all
    assert rows[0][0] == "NEW"
    assert len(selectable(db)) == 1


def test_loop_repeat_runs_never_create_duplicate_missions(db, tmp_path):
    """The verifier's end-to-end scenario: two identical loop runs over
    a stale claim must not select duplicate missions with the same
    objective (run 2 must find nothing new to select)."""
    from jarvis_foundry.v3_loop import run_one_loop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from jarvis_foundry.roles.router import RoleRouter
    from test_v3_loop import WritingFakeHermes

    db.append(Event.create(
        "project.registered",
        {"resource_id": "resource-test", "kind": "git_worktree",
         "root_path": str(tmp_path), "resolved_root": str(tmp_path),
         "baseline": {"branch": "main", "head": "x", "remotes": [],
                      "status_porcelain": []},
         "capabilities": {
             "write_registered_worktree_files": "READY",
             "read_registered_files": "READY"},
         "readiness_state": "READY"},
        source_type="test", source_id="test-suite",
        idempotency_key="project.registered:resource-test"))
    # one stale claim -> one research item
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": "claim-stale1", "mission_id": "mission-1",
         "statement": "The scheduler detail 7 is recorded.",
         "claim_type": "fact", "confidence": 0.5,
         "fresh_until": "2020-01-01T00:00:00Z",
         "source": {"run_id": "r:1", "event_id": "ghost.md:abc"}},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_recorded:claim-stale1"))
    for run in ("a", "b"):
        report = run_one_loop(
            db, loop_id=f"run-{run}", adapter=WritingFakeHermes(),
            router=RoleRouter(), mission_id="mission-1",
            resource_id="resource-test", target_root=tmp_path / "target",
            max_items=10, max_bytes=20_000)
        if run == "a":
            assert report["monitors"]["system"]["candidates_found"] >= 1
        else:
            assert report["monitors"]["system"]["candidates_found"] == 0
    with db.connect() as connection:
        missions = connection.execute(
            "SELECT COUNT(*) FROM missions").fetchone()[0]
        selectable = connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status IN "
            "('NEW', 'SCORED', 'APPROVED')").fetchone()[0]
        executed = connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status = "
            "'EXECUTED'").fetchone()[0]
        rejected = connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status = "
            "'REJECTED'").fetchone()[0]
    # the verifier's contract: repeated identical cycles never INCREASE
    # selectable work and never create a second mission for the same
    # objective — the monitor candidate is selected once, the
    # maintenance candidate once; everything else is an inert REJECTED
    # duplicate RECORD (historical, never selectable)
    assert selectable == 1
    assert executed == 1
    assert missions == 1  # one mission ever — no duplicate missions
    assert rejected >= 2  # duplicate records preserved, inert


def test_rejected_low_value_items_never_resurrect(db, tmp_path):
    """MEDIUM finding: a queue-REJECTED WEAK item (confidence below the
    low-value floor) must never become a candidate via the poll feed."""
    from jarvis_foundry.v3_loop import run_one_loop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from jarvis_foundry.roles.router import RoleRouter
    from test_v3_loop import WritingFakeHermes

    db.append(Event.create(
        "project.registered",
        {"resource_id": "resource-test", "kind": "git_worktree",
         "root_path": str(tmp_path), "resolved_root": str(tmp_path),
         "baseline": {"branch": "main", "head": "x", "remotes": [],
                      "status_porcelain": []},
         "capabilities": {
             "write_registered_worktree_files": "READY",
             "read_registered_files": "READY"},
         "readiness_state": "READY"},
        source_type="test", source_id="test-suite",
        idempotency_key="project.registered:resource-test"))
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": "claim-trivia", "mission_id": "mission-1",
         "statement": "The color of the button is blue.",
         "claim_type": "fact", "confidence": 0.03,
         "source": {"run_id": "r:1", "event_id": "ghost.md:abc"}},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_recorded:claim-trivia"))
    report = run_one_loop(
        db, loop_id="run-trivia", adapter=WritingFakeHermes(),
        router=RoleRouter(), mission_id="mission-1",
        resource_id="resource-test", target_root=tmp_path / "target",
        max_items=10, max_bytes=20_000)
    with db.connect() as connection:
        weak_resurrected = connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE source LIKE "
            "'monitor:WEAK%'").fetchone()[0]
        rejected_items = connection.execute(
            "SELECT COUNT(*) FROM research_queue WHERE status = "
            "'REJECTED'").fetchone()[0]
    # the REJECTED low-value item never resurrects via the poll feed —
    # the claim's genuinely-OPEN UNSUPPORTED signal (its source does not
    # exist) is what the poll surfaced and the queue resolved honestly
    assert weak_resurrected == 0
    assert rejected_items >= 1  # the queue itself rejected it honestly
    assert report["monitors"]["system"]["candidates_found"] >= 1


def test_duplicate_candidate_row_never_selectable(db):
    """Duplicate candidate_generated events must not insert selectable
    NEW rows — the row is recorded (history) but REJECTED."""
    first = generate_candidate(db, "mission-1", "vault",
                               "Add a retry budget to the gateway")
    dup = generate_candidate(db, "mission-1", "monitor:vault",
                             "  ADD a RETRY budget to the GATEWAY.  ")
    assert dup["created"] is False
    with db.connect() as connection:
        status = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (dup["candidate_id"],)).fetchone()[0]
    assert status == "REJECTED"
    assert dup["candidate_id"] not in selectable(db)
    assert first["candidate_id"] in selectable(db)


def test_poll_unknown_monitor_fails_atomically(db):
    """A discovery.monitor_polled event for a nonexistent monitor must
    fail the append (projection guard) — no silent 0-row UPDATE."""
    monitor_id = register_monitor(db, "mission-1", "vault", "root")
    with pytest.raises(ValueError):
        poll_monitor(db, "mon-doesnotexist", [{"objective": "x"}])
    # direct event for an unknown monitor
    with pytest.raises(ValueError):
        db.append(Event.create(
            "discovery.monitor_polled",
            {"monitor_id": "mon-doesnotexist", "items_seen": 1,
             "candidates_found": 0},
            source_type="test", source_id="test-suite",
            idempotency_key="discovery.monitor_polled:mon-doesnotexist:x"))
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.monitor_polled'").fetchone()[0]
    assert count == 0  # atomic rollback — nothing entered the ledger


def test_empty_objective_never_fabricates(db):
    monitor_id = register_monitor(db, "mission-1", "vault", "root")
    result = poll_monitor(db, monitor_id, [
        {"objective": "   ", "mission_id": "mission-1"},
        {"objective": "Real objective.", "mission_id": "mission-1"},
    ])
    assert result["items_seen"] == 2
    assert result["candidates_found"] == 1
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT objective FROM candidates").fetchall()
    assert [r[0] for r in rows] == ["Real objective."]


def test_poll_retry_dedupes_on_stable_key(db):
    """A crash-retried poll with the same poll_key appends one event;
    a distinct poll (different key) appends another."""
    monitor_id = register_monitor(db, "mission-1", "vault", "root")
    poll_monitor(db, monitor_id, [], poll_key="cycle-7")
    poll_monitor(db, monitor_id, [], poll_key="cycle-7")
    poll_monitor(db, monitor_id, [], poll_key="cycle-8")
    with db.connect() as connection:
        events = connection.execute(
            "SELECT idempotency_key FROM events WHERE event_type = "
            "'discovery.monitor_polled' ORDER BY sequence").fetchall()
    assert len(events) == 2
    assert events[0][0] != events[1][0]
