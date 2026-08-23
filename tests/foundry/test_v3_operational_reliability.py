"""WP-D: vertical-slice reliability pass.

Covers the required-matrix items not owned by the other suites:
concurrent snapshot reads during mutation windows, timezone/timestamp
ordering with mixed offsets, duplicate mission prevention across a
repeated reconciliation, and deterministic newest-cycle selection.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    return database


def _seed_candidate(db, candidate_id, updated_at, status="NEW"):
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO candidates (candidate_id, mission_id, source,"
            " objective, objective_canonical, evidence_json, status,"
            " last_event_id, updated_at)"
            " VALUES (?, 'm', 'test', 'obj', 'obj', '[]', ?, ?, ?)",
            (candidate_id, status, f"e:{candidate_id}", updated_at))


def _seed_lineage_mission(db, candidate_id, mission_id):
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO missions (mission_id, objective, resource_id, state,"
            " acceptance_json, next_action, created_from, last_event_id,"
            " updated_at)"
            " VALUES (?, 'o', 'r', 'COMPLETE', '[]', NULL, ?, ?,"
            " '2026-08-22T10:00:00Z')",
            (mission_id, f"candidate:{candidate_id}",
             f"m:{mission_id}"))


def _record_completed_pass(db, mission_id):
    db.append(Event.create(
        "mission.completed",
        {"mission_id": mission_id, "outcome": "PASSED", "run_id": "r"},
        source_type="supervisor", source_id="reliability",
        idempotency_key=f"mission.completed:{mission_id}"))


def test_concurrent_snapshot_reads_during_outcome_reconciliation(db):
    """Snapshot reads during a reconciliation burst stay consistent."""
    _seed_candidate(db, "cand-conc", "2026-08-22T10:00:00Z", status="ELIGIBLE")
    _seed_lineage_mission(db, "cand-conc", "M-CONC")
    _record_completed_pass(db, "M-CONC")

    from jarvis_foundry.v3_outcomes import reconcile_pending_outcomes

    errors: list[Exception] = []
    observations: list[str] = []

    def reader():
        try:
            for _ in range(20):
                snap = build_snapshot(db)
                discovery = snap["discovery"]
                # Every read must be internally consistent JSON.
                json.dumps(snap)
                observations.append(discovery["state"])
        except Exception as error:  # noqa: BLE001 - collected below
            errors.append(error)

    def writer():
        try:
            for _ in range(10):
                reconcile_pending_outcomes(db)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    threads.append(threading.Thread(target=writer))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    # Exactly-once still holds after concurrent reconciliation.
    outcomes = [e for e in db.events()
                if e.event_type == "discovery.candidate_outcome_recorded"]
    assert len(outcomes) == 1
    assert observations  # readers actually produced snapshots


def test_duplicate_prevention_across_repeated_reconciliation(db):
    """Repeating reconcile never duplicates missions or outcomes."""
    from jarvis_foundry.v3_outcomes import reconcile_pending_outcomes

    _seed_candidate(db, "cand-dup", "2026-08-22T10:00:00Z", status="EXECUTED")
    _seed_lineage_mission(db, "cand-dup", "M-DUP")
    _record_completed_pass(db, "M-DUP")
    for _ in range(5):
        result = reconcile_pending_outcomes(db)
    assert result["reconciled"] == 0
    outcomes = [e for e in db.events()
                if e.event_type == "discovery.candidate_outcome_recorded"]
    assert len(outcomes) == 1
    missions = [e for e in db.events() if e.event_type == "mission.created"]
    assert len(missions) <= 1  # no new lineage mission ever invented here


def test_timestamp_ordering_with_mixed_timezones(db):
    """Mixed-offset stamps order by string sort (SQLite storage reality).

    Canonical writers store UTC Z timestamps; the projection's ordering is
    therefore deterministic for all canonical data. This test pins that
    documented behavior: string-ordered, tie-broken by candidate_id DESC —
    never crashing and never re-ordering nondeterministically.
    """
    base = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
    stamps = [
        base.isoformat(),                                # 12:00+00
        (base + timedelta(seconds=1)).isoformat(),       # 12:00:01+00
        "2026-08-22T14:30:00+05:30",
        "2026-08-22T15:00:00+03:00",
        (base - timedelta(days=1)).isoformat(),
    ]
    for i, stamp in enumerate(stamps):
        _seed_candidate(db, f"cand-tz-{i}", stamp)

    discovery = build_snapshot(db)["discovery"]
    ids = [c["candidate_id"] for c in discovery["candidates_recent"]]
    expected = [f"cand-tz-{i}" for i in sorted(
        range(len(stamps)),
        key=lambda i: (stamps[i], f"cand-tz-{i}"),
        reverse=True,
    )]
    assert ids == expected and len(ids) == 5
    # Deterministic across rebuilds.
    again = build_snapshot(db)["discovery"]
    assert [c["candidate_id"] for c in again["candidates_recent"]] == ids


def test_newest_cycle_selection_deterministic_under_sequence_ties(db):
    """Cycle selection follows ledger sequence, not string timestamps."""
    now = "2026-08-22T10:00:00Z"
    # Append cycles whose recorded_at strings are NOT monotonic.
    payloads = [
        ("L-old", "COMPILED", "2026-08-21T09:00:00Z"),
        ("L-new", "WAITING", "2026-08-01T00:00:00Z"),   # older timestamp!
        ("L-mid", "BLOCKED", "2026-08-25T23:59:59Z"),   # future timestamp!
    ]
    for loop_id, outcome, recorded in payloads:
        event = Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": outcome, "observed": 1,
             "candidates_created": 0, "decision_id": None,
             "degraded_sources": [], "reason": None},
            source_type="supervisor", source_id="reliability",
            idempotency_key=f"discovery.cycle_completed:{loop_id}")
        db.append(event)
        # Force the recorded_at column to a specific value post-append.
        with db.connect() as connection:
            connection.execute(
                "UPDATE events SET recorded_at = ? WHERE idempotency_key = ?",
                (recorded, f"discovery.cycle_completed:{loop_id}"))

    discovery = build_snapshot(db)["discovery"]
    # Ledger-sequence order wins, not recorded_at strings: L-new was
    # appended after L-old even though its timestamp string sorts earliest.
    assert discovery["latest_cycle"]["loop_id"] == "L-mid"
    assert [c["loop_id"] for c in discovery["cycles"]] == [
        "L-mid", "L-new", "L-old"]
    # The discriminator: naive recorded_at sorting would put L-new LAST.
    assert discovery["cycles"][-1]["loop_id"] != "L-new"


def test_no_leaked_handles_after_many_snapshot_builds(db, tmp_path):
    """Repeated builds over many fresh DBs leave no uncollectable junk."""
    import gc

    databases = []
    try:
        for i in range(8):
            database = FoundryDatabase(tmp_path / f"leak-{i}.sqlite")
            database.initialize()
            _seed_candidate(database, f"cand-{i}", "2026-08-22T10:00:00Z")
            build_snapshot(database)
            databases.append(database)
        gc.collect()
        # All handles close cleanly via context managers inside the builder;
        # reopening every file proves nothing was left exclusively locked.
        for i, database in enumerate(databases):
            reopened = FoundryDatabase(tmp_path / f"leak-{i}.sqlite")
            with reopened.connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM candidates").fetchone()[0]
            assert count == 1
    finally:
        del databases
        gc.collect()


def test_snapshot_survives_untrusted_unicode_and_control_text(db):
    """Hostile candidate text flows through projection + JSON round-trip."""
    values = [
        "null", "true", '{"json":"like"}', "</script><b>bold</b>",
        "🚀 emoji \u2028 line-sep", "%s %d {format}", "x' OR '1'='1",
    ]
    for i, value in enumerate(values):
        _seed_candidate(db, f"cand-h{i}", "2026-08-22T10:00:00Z")
        with db.connect() as connection:
            connection.execute(
                "UPDATE candidates SET objective = ? WHERE candidate_id = ?",
                (value, f"cand-h{i}"))
    snapshot = build_snapshot(db)
    serialized = json.dumps(snapshot, ensure_ascii=True)
    reparsed = json.loads(serialized)
    objectives = {c["objective"] for c in reparsed["discovery"]["candidates_recent"]}
    assert set(values) <= objectives
