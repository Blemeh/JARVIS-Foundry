"""V3 WP0 fixture tests: policy, baseline DB generator, bare remote."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.migrations import SCHEMA_VERSION

from v3_fixtures import (CANONICAL_V2_SCHEMA_VERSION, POLICY_VERSION,
                         V3_SCHEMA_VERSION, load_policy, ls_remote,
                         make_bare_remote, make_baseline_db)


def test_policy_fixture_matches_frozen_decisions():
    policy = load_policy()
    assert policy["policy_version"] == POLICY_VERSION == "v3-policy-2026-08-21"
    assert policy["thresholds"]["min_total_score"] == 0.40
    assert abs(sum(policy["weights"].values()) - 1.0) < 1e-9
    assert set(policy["weights"]) == {
        "strategic_fit", "expected_value", "information_gain", "urgency",
        "confidence", "effort", "cost", "risk", "reversibility",
        "opportunity_cost"}
    assert policy["hard_filters"]["max_blast_radius"] == 0.85


def test_schema_constants_derive_from_canonical_source():
    # Canonical origin/main merged V2 (f1660f6) at v35; V3 owned 36;
    # software-intelligence (Lane 4, 2026-08-24) added additive v37.
    assert CANONICAL_V2_SCHEMA_VERSION == 35
    assert V3_SCHEMA_VERSION == 36
    assert SCHEMA_VERSION >= V3_SCHEMA_VERSION


def test_baseline_db_generator_produces_pre_v2_merge_database(tmp_path):
    db_path = make_baseline_db(tmp_path / "base.db")
    connection = sqlite3.connect(db_path)
    try:
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        assert version == CANONICAL_V2_SCHEMA_VERSION
        missions = connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0]
        candidates = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        scores = connection.execute(
            "SELECT COUNT(*) FROM candidate_scores").fetchone()[0]
        assert (missions, candidates, scores) == (2, 1, 1)
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(candidates)")}
        assert "fingerprint" not in columns
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='candidate_outcomes'"
        ).fetchone()
    finally:
        connection.close()


def test_baseline_db_migrates_forward_to_v3_schema(tmp_path):
    db_path = make_baseline_db(tmp_path / "base.db")
    database = FoundryDatabase(db_path)
    version = database.initialize()
    assert version == SCHEMA_VERSION
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT candidate_id, status FROM candidates").fetchall()
        assert len(rows) == 1
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
        policies = connection.execute(
            "SELECT COUNT(*) FROM scoring_policy_registry").fetchone()[0]
        portfolio = connection.execute(
            "SELECT COUNT(*) FROM discovery_portfolio").fetchone()[0]
        assert (outcomes, policies, portfolio) == (0, 0, 0)


def test_disposable_bare_remote_push_and_ls_remote_roundtrip(tmp_path):
    bare = make_bare_remote(tmp_path)
    assert bare.name == "origin.git"
    seed_head = ls_remote(bare)
    assert seed_head and len(seed_head) == 40

    work = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / "proof.txt").write_text("pushed by v3 fixture test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "proof.txt"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "proof"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)

    advanced = ls_remote(bare)
    assert advanced != seed_head
