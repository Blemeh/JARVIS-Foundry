"""V4-S1: candidate supersession (additive correction) + migration v17.

DIR-2026-08-12 item 5: preserve history but quarantine, invalidate or
supersede polluted projections additively. Candidates whose premise was
false (e.g. the pre-S1 unsupported-claims signal that produced
"Maintenance: verify and fix the N unsupported claims" candidates) are
superseded via `discovery.candidate_superseded` — never deleted, never
selected (selection scans NEW/SCORED only).
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import (
    generate_candidate,
    score_candidate,
    supersede_candidate,
)
from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.v3_loop import _top_scorable


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def seeded_candidate(db: FoundryDatabase) -> str:
    result = generate_candidate(db, "mission-1", "maintenance",
                                "Maintenance: verify and fix the 813 "
                                "unsupported claims (0 stale, 0 open) — "
                                "root cause unknown, regression risk today",
                                evidence=[{"counts": {"unsupported": 813}}])
    assert result["created"] is True
    score_candidate(db, result["candidate_id"])
    return result["candidate_id"]


def test_supersede_flips_status_and_excludes_from_selection(db):
    candidate_id = seeded_candidate(db)
    with db.connect() as connection:
        before = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()[0]
    assert before == "SCORED"

    supersede_candidate(db, candidate_id,
                        superseded_by="cand-00d0b3a03872",
                        reason="premise re-measured false after the "
                               "source-kind-aware support repair")

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, superseded_by FROM candidates "
            "WHERE candidate_id = ?", (candidate_id,)).fetchone()
    assert row[0] == "SUPERSEDED"
    assert row[1] == "cand-00d0b3a03872"
    # selection scans exclude SUPERSEDED
    top = _top_scorable(db, budget=10)
    assert candidate_id not in {c["candidate_id"] for c in top}


def test_supersede_idempotent(db):
    candidate_id = seeded_candidate(db)
    supersede_candidate(db, candidate_id, superseded_by="x", reason="r1")
    supersede_candidate(db, candidate_id, superseded_by="x", reason="r1")
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.candidate_superseded'").fetchone()[0]
        status = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()[0]
    assert rows == 1
    assert status == "SUPERSEDED"


def test_migration_v17_widens_check_and_preserves_rows(tmp_path):
    """The v16->v17 rebuild must preserve every candidate row, widen the
    status CHECK with SUPERSEDED, and add superseded_by."""
    # build a database at EXACTLY v16 (migrate with an explicit target —
    # the WAL-safe way; copy2 of a WAL-mode DB would miss committed pages)
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as con:
        from jarvis_foundry.migrations import migrate
        migrate(con, "2026-08-13T00:00:00Z", target=16)
    database = FoundryDatabase(old)
    candidate_id = seeded_candidate(database)
    with database.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == 16
    # the v16 CHECK rejects SUPERSEDED (proves the constraint existed)
    with database.connect() as connection:
        try:
            connection.execute(
                "UPDATE candidates SET status = 'SUPERSEDED' "
                "WHERE candidate_id = ?", (candidate_id,))
            connection.commit()
            rejected = False
        except sqlite3.IntegrityError:
            rejected = True
    assert rejected is True

    # migrate the v16 copy with the CURRENT code (applies v17)
    migrated = FoundryDatabase(old)
    migrated.initialize()
    with migrated.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        row = connection.execute(
            "SELECT status, superseded_by FROM candidates "
            "WHERE candidate_id = ?", (candidate_id,)).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert version == SCHEMA_VERSION
    assert row[0] == "SCORED" and row[1] is None
    assert count == 1
    # the widened CHECK accepts SUPERSEDED on the migrated copy
    migrated.append(Event.create(
        "discovery.candidate_superseded",
        {"candidate_id": candidate_id, "superseded_by": "y",
         "reason": "post-migration supersession"},
        source_type="test", source_id="test-suite",
        idempotency_key="discovery.candidate_superseded:" + candidate_id))
    with migrated.connect() as connection:
        status = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()[0]
    assert status == "SUPERSEDED"
